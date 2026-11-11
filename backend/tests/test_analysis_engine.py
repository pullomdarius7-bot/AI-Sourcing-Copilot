"""Analysis engine v2 (pure: no database needed). Every expected number below is worked out by hand."""
import pytest

from app.analysis import engine
from app.rfqs import comparison as cmp
from tests.rfq_fixtures import base_inputs, line, quote


def rank_of(result, sid):
    return next(r for r in result["ranking"] if r["supplier_id"] == sid)


def codes(result, sid):
    return {d["code"] for d in rank_of(result, sid)["deviations"]}


def test_ranking_scores_and_basket_are_like_for_like():
    res = cmp.compare(base_inputs())
    assert res["engine_version"] == engine.ENGINE_VERSION and res["decision"] == "human"
    assert "approved by a person" in res["decision_note"]
    assert res["basket_item_ids"] == ["i1", "i2"]
    totals = {r["supplier_id"]: r["basket_total"] for r in res["ranking"]}
    assert totals == {"s1": pytest.approx(22500.0), "s2": pytest.approx(22000.0), "s3": pytest.approx(20750.0)}
    s3 = rank_of(res, "s3")
    assert s3["criteria"]["price"]["score"] == 1.0 and s3["criteria"]["qualification"]["score"] == 0.0
    assert "supplier_not_qualified" in s3["flags"] and s3["risk"]["counts"]["critical"] == 1
    assert [r["rank"] for r in res["ranking"]] == [1, 2, 3]
    assert any("2 of 3 lines" in f for f in res["global_flags"])
    assert any("Incoterms differ" in f for f in res["global_flags"])


def test_scores_are_reproducible_and_bounded():
    a, b = cmp.compare(base_inputs()), cmp.compare(base_inputs())
    assert a == b
    for r in a["ranking"]:
        assert 0 <= r["score"] <= 100 and 0 <= r["risk"]["score"] <= 100
        assert all(c["score"] is None or 0 <= c["score"] <= 1 for c in r["criteria"].values())


def test_total_score_is_the_weighted_sum_shown():
    res = cmp.compare(base_inputs())
    for r in res["ranking"]:
        expected = sum(res["active_weights"][k] * (r["criteria"][k]["score"] or 0) for k in res["active_weights"]) * 100
        assert r["score"] == pytest.approx(expected, abs=0.01)


def test_explanations_cover_every_criterion():
    for r in cmp.compare(base_inputs())["ranking"]:
        text = " ".join(r["explanation"])
        for word in ("Price:", "Lead time:", "Coverage:", "Qualification:", "Risk:"):
            assert word in text


def test_weights_change_the_ranking_and_are_normalised():
    price_only = cmp.compare(base_inputs(), {"price": 1})
    assert price_only["ranking"][0]["supplier_id"] == "s3" and price_only["weights"]["price"] == 1.0
    qual_heavy = cmp.compare(base_inputs(), {"price": 1, "lead_time": 1, "coverage": 1, "qualification": 5, "risk": 1})
    assert qual_heavy["ranking"][-1]["supplier_id"] == "s3"
    assert sum(qual_heavy["weights"].values()) == pytest.approx(1.0)


@pytest.mark.parametrize("weights,message", [
    ({"price": -1}, "negative"), ({"price": 0}, "positive"), ({"vibes": 1}, "Unknown criteria"),
])
def test_invalid_weights_are_rejected(weights, message):
    with pytest.raises(cmp.ComparisonError, match=message):
        cmp.compare(base_inputs(), weights)


# --- Currency conversion -----------------------------------------------------------------------------
def test_foreign_currency_is_converted_with_recorded_rate():
    inputs = base_inputs()
    inputs["quotes"][1]["currency"] = "USD"
    inputs["fx_rates"] = [{"base": "EUR", "quote": "USD", "rate": "1.25", "valid_from": "2026-11-01"},
                          {"base": "EUR", "quote": "USD", "rate": "9", "valid_from": "2026-12-01"}]  # future: ignored
    res = cmp.compare(inputs)
    cell = res["items"][0]["cells"]["s2"]
    assert cell["unit_price"] == pytest.approx(3.2)  # 4.00 USD / 1.25
    assert cell["comparable_total"] == pytest.approx(16000) and "inverted" in cell["fx"]
    assert res["fx_used"]["USD"]["age_days"] == 9
    assert "currency_converted" in codes(res, "s2")


def test_missing_rate_excludes_and_stale_rate_is_flagged():
    inputs = base_inputs()
    inputs["quotes"][2]["currency"] = "USD"
    res = cmp.compare(inputs)
    assert {e["supplier_id"]: e["reason"] for e in res["excluded"]} == {"s3": "no_fx_rate"}
    inputs["fx_rates"] = [{"base": "USD", "quote": "EUR", "rate": "0.8", "valid_from": "2026-01-01"}]
    res = cmp.compare(inputs)
    assert "fx_rate_stale" in rank_of(res, "s3")["flags"]


def test_unverified_and_unknown_currency_and_missing_quotes():
    inputs = base_inputs()
    inputs["quotes"][0]["currency"] = None
    inputs["quotes"][1]["status"] = "draft"
    inputs["quotes"].pop(2)
    reasons = {e["supplier_id"]: e["reason"] for e in cmp.compare(inputs)["excluded"]}
    assert reasons == {"s1": "currency_unknown", "s2": "not_verified", "s3": "no_quote"}


# --- Unit conversion --------------------------------------------------------------------------------
def test_standard_unit_conversion_divides_price_and_multiplies_quantity():
    inputs = base_inputs()
    inputs["items"][0].update(unit="kg", quantity=500)
    inputs["quotes"][0]["lines"][0].update(unit="g", unit_price=0.005, quantity=500000, moq=100000)
    cell = cmp.compare(inputs)["items"][0]["cells"]["s1"]
    assert cell["unit_price"] == pytest.approx(5.0) and cell["comparable_total"] == pytest.approx(2500)
    assert cell["moq"] == pytest.approx(100) and "unit_converted" in cell["flags"] and "quantity_differs" not in cell["flags"]


def test_unconvertible_units_need_an_organization_rule():
    inputs = base_inputs()
    inputs["quotes"][1]["lines"][0]["unit"] = "kg"
    res = cmp.compare(inputs)
    assert res["items"][0]["cells"]["s2"]["comparable_total"] is None
    assert "unit_not_convertible" in rank_of(res, "s2")["flags"]
    inputs["unit_conversions"] = [{"from_unit": "pcs", "to_unit": "kg", "factor": "0.05"}]  # 1 piece weighs 50 g
    cell = cmp.compare(inputs)["items"][0]["cells"]["s2"]
    assert cell["unit_price"] == pytest.approx(0.2)  # 4.00 per kg x 0.05 kg per piece
    assert "organization rule" in cell["conversions"][0]


def test_unit_aliases_are_equivalent_and_missing_unit_is_flagged():
    inputs = base_inputs()
    inputs["quotes"][1]["lines"][0]["unit"] = "Pieces"
    inputs["quotes"][0]["lines"][0]["unit"] = None
    res = cmp.compare(inputs)
    assert res["items"][0]["cells"]["s2"]["comparable_total"] == pytest.approx(20000)
    assert "unit_not_stated" in res["items"][0]["cells"]["s1"]["flags"]


# --- MOQ ---------------------------------------------------------------------------------------------------
def test_moq_above_quantity_raises_payable_total_and_reports_excess():
    inputs = base_inputs()
    inputs["quotes"][2]["lines"][0]["moq"] = 10000
    res = cmp.compare(inputs)
    cell = res["items"][0]["cells"]["s3"]
    assert cell["payable_quantity"] == 10000 and cell["requested_total"] == pytest.approx(19500)
    assert cell["comparable_total"] == pytest.approx(39000) and cell["moq_excess"] == pytest.approx(19500)
    s3 = rank_of(res, "s3")
    assert s3["moq_excess_total"] == pytest.approx(19500) and "moq_above_quantity" in s3["flags"]
    assert next(d for d in s3["deviations"] if d["code"] == "moq_above_quantity")["severity"] == "major"
    assert res["moq_analysis"]["suppliers_with_excess"] == [{"supplier_name": "Orion Cast", "excess": 19500.0}]
    assert res["ranking"][0]["supplier_id"] != "s3"


def test_moq_at_or_below_quantity_changes_nothing():
    inputs = base_inputs()
    inputs["quotes"][2]["lines"][0]["moq"] = 1000
    cell = cmp.compare(inputs)["items"][0]["cells"]["s3"]
    assert cell["comparable_total"] == pytest.approx(19500) and cell["moq_excess"] is None


# --- Lead time ---------------------------------------------------------------------------------------------
def test_lead_time_against_required_date():
    inputs = base_inputs()
    inputs["required_by"] = "2026-12-08"  # 28 days after as_of
    res = cmp.compare(inputs)
    lt = res["lead_time_analysis"]
    assert lt["available_days"] == 28 and lt["fastest"] == 14 and lt["slowest"] == 35
    assert lt["late_suppliers"] == ["Kessler GmbH"]
    s2 = rank_of(res, "s2")
    assert s2["on_time"] is False and s2["slack_days"] == -7 and "lead_time_exceeds_required" in s2["flags"]
    assert rank_of(res, "s1")["on_time"] is True


def test_missing_lead_time_scores_zero_and_says_so():
    inputs = base_inputs()
    for ln in inputs["quotes"][1]["lines"]:
        ln["lead_time_days"] = None
    s2 = rank_of(cmp.compare(inputs), "s2")
    assert s2["criteria"]["lead_time"]["score"] == 0.0 and "lead_time_not_stated" in s2["flags"]
    assert any("not assumed" in x for x in s2["explanation"])


# --- Deviations and risk ----------------------------------------------------------------------------------
def test_requested_terms_deviations():
    inputs = base_inputs()
    inputs["requested_incoterms"] = "DAP"
    inputs["requested_payment_terms"] = "net 30"
    inputs["quotes"][1]["payment_terms"] = "Net 60"
    res = cmp.compare(inputs)
    assert "incoterms_differ" in codes(res, "s3") and "incoterms_differ" not in codes(res, "s1")
    assert "payment_terms_differ" in codes(res, "s2") and "payment_terms_differ" not in codes(res, "s1")


def test_terms_and_validity_deviations():
    inputs = base_inputs()
    inputs["quotes"][0].update(incoterms=None, payment_terms=None, valid_until="2026-01-01")
    assert {"incoterms_not_stated", "payment_terms_not_stated", "quote_expired"} <= codes(cmp.compare(inputs), "s1")
    inputs["quotes"][0]["valid_until"] = "2026-11-12"
    assert "validity_ends_soon" in codes(cmp.compare(inputs), "s1")
    inputs["quotes"][0]["valid_until"] = None
    assert "validity_not_stated" in codes(cmp.compare(inputs), "s1")


def test_risk_points_and_levels():
    assert engine.risk_from([]) == {"score": 0, "level": "low", "counts": {"critical": 0, "major": 0, "minor": 0, "info": 0}, "factors": []}
    devs = [{"severity": "critical", "message": "a"}, {"severity": "major", "message": "b"}, {"severity": "minor", "message": "c"}]
    risk = engine.risk_from(devs)
    assert risk["score"] == 38 and risk["level"] == "medium" and len(risk["factors"]) == 3
    assert engine.risk_from([{"severity": "critical", "message": "x"}] * 5)["score"] == 100
    s3 = rank_of(cmp.compare(base_inputs()), "s3")
    assert s3["risk"]["score"] == 25 + 3 and s3["criteria"]["risk"]["score"] == pytest.approx(0.72)


def test_unquoted_mandatory_line_is_critical_and_coverage_weighted_by_priority():
    res = cmp.compare(base_inputs())
    s2 = rank_of(res, "s2")
    dev = next(d for d in s2["deviations"] if d["code"] == "line_not_quoted")
    assert dev["severity"] == "critical" and dev["line"] == 3
    assert s2["criteria"]["coverage"]["score"] == pytest.approx(0.6)  # (2 + 1) of (2 + 1 + 2)


def test_unconfirmed_matches_are_ignored_and_reported():
    inputs = base_inputs()
    inputs["quotes"][0]["lines"][0]["match_status"] = "suggested"
    res = cmp.compare(inputs)
    assert res["items"][0]["cells"]["s1"]["flags"] == ["not_quoted"]
    assert "i1" not in res["basket_item_ids"] and "extra_line" in codes(res, "s1")


def test_quantity_differences_and_total_only_lines():
    inputs = base_inputs()
    inputs["quotes"][0]["lines"][0].update(quantity=1000)
    cell = cmp.compare(inputs)["items"][0]["cells"]["s1"]
    assert cell["comparable_total"] == pytest.approx(21000) and "quantity_differs" in cell["flags"]
    inputs["quotes"][0]["lines"][0].update(unit_price=None, total_price=4400, quantity=1000)
    cell = cmp.compare(inputs)["items"][0]["cells"]["s1"]
    assert cell["comparable_total"] == pytest.approx(22000) and "unit_price_from_total" in cell["flags"]
    inputs["quotes"][0]["lines"][0].update(quantity=None)
    cell = cmp.compare(inputs)["items"][0]["cells"]["s1"]
    assert cell["comparable_total"] is None and "price_not_comparable" in cell["flags"]


def test_outliers_arithmetic_confidence_and_duplicates():
    inputs = base_inputs()
    inputs["quotes"][2]["lines"][0].update(unit_price=1.0)
    inputs["quotes"][0]["lines"][1].update(total_price=999, confidence=0.4)
    res = cmp.compare(inputs)
    assert "price_outlier_low" in res["items"][0]["cells"]["s3"]["flags"]
    assert {"arithmetic_mismatch", "low_confidence"} <= set(res["items"][1]["cells"]["s1"]["flags"])
    assert res["items"][0]["median_unit_price"] == pytest.approx(4.0) and res["items"][0]["lowest_unit_price"] == 1.0
    inputs = base_inputs()
    inputs["quotes"][0]["lines"].append(line("i1", 0.5, qty=5000))
    assert cmp.compare(inputs)["items"][0]["cells"]["s1"]["flags"] == ["multiple_lines_for_item"]


def test_no_common_basket_drops_price_and_redistributes_weight():
    inputs = base_inputs()
    inputs["quotes"] = [quote("s1", [line("i1", 4.2, qty=5000, lead=21)]), quote("s2", [line("i3", 5, unit=None, lead=10)])]
    res = cmp.compare(inputs)
    assert res["basket_item_ids"] == [] and "price" not in res["active_weights"]
    assert sum(res["active_weights"].values()) == pytest.approx(1.0)
    assert any("price is not scored" in f for f in res["global_flags"])


def test_near_ties_and_stable_order():
    inputs = base_inputs()
    inputs["quotes"] = [quote("s1", [line("i1", 4.0, qty=5000, lead=20)]), quote("s2", [line("i1", 4.0, qty=5000, lead=20)])]
    res = cmp.compare(inputs)
    assert res["ranking"][0]["score"] == res["ranking"][1]["score"]
    assert [r["supplier_name"] for r in res["ranking"]] == ["Kessler GmbH", "Northwind Metals"]
    assert any("within 2 points" in f for f in res["global_flags"])


def test_inputs_hash_tracks_data_rates_and_rules_but_not_weights():
    base = cmp.compare(base_inputs())
    assert cmp.compare(base_inputs(), {"price": 1})["inputs_sha256"] == base["inputs_sha256"]
    for mutate in (lambda i: i["quotes"][0]["lines"][0].update(unit_price=4.21),
                   lambda i: i.update(fx_rates=[{"base": "EUR", "quote": "USD", "rate": "1.1", "valid_from": "2026-11-01"}]),
                   lambda i: i.update(unit_conversions=[{"from_unit": "box", "to_unit": "pcs", "factor": "10"}]),
                   lambda i: i.update(required_by="2026-12-31")):
        changed = base_inputs()
        mutate(changed)
        assert cmp.compare(changed)["inputs_sha256"] != base["inputs_sha256"]


def test_empty_comparison():
    inputs = base_inputs()
    inputs["quotes"] = []
    res = cmp.compare(inputs)
    assert res["ranking"] == [] and res["highest_score_supplier_id"] is None
    assert {e["reason"] for e in res["excluded"]} == {"no_quote"}

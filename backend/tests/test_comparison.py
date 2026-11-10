"""Comparison engine (pure: no database needed)."""
import pytest

from app.rfqs import comparison as cmp
from tests.rfq_fixtures import base_inputs, line, quote


def rank_of(result, sid):
    return next(r for r in result["ranking"] if r["supplier_id"] == sid)


def test_ranking_scores_and_basket_are_like_for_like():
    res = cmp.compare(base_inputs())
    assert res["decision"] == "human" and "does not award" in res["decision_note"]
    assert res["basket_item_ids"] == ["i1", "i2"]  # s2 did not price i3, so i3 is outside the basket
    totals = {r["supplier_id"]: r["basket_total"] for r in res["ranking"]}
    assert totals == {"s1": pytest.approx(22500.0), "s2": pytest.approx(22000.0), "s3": pytest.approx(20750.0)}
    s3 = rank_of(res, "s3")
    assert s3["criteria"]["price"]["score"] == 1.0 and s3["criteria"]["qualification"]["score"] == 0.0
    assert "supplier_not_qualified" in s3["flags"]
    assert [r["rank"] for r in res["ranking"]] == [1, 2, 3]
    assert res["highest_score_supplier_id"] == res["ranking"][0]["supplier_id"]
    assert any("2 of 3 lines" in f for f in res["global_flags"])
    assert any("Incoterms differ" in f for f in res["global_flags"])


def test_scores_are_reproducible_and_bounded():
    a, b = cmp.compare(base_inputs()), cmp.compare(base_inputs())
    assert a == b
    for r in a["ranking"]:
        assert 0 <= r["score"] <= 100
        assert all(c["score"] is None or 0 <= c["score"] <= 1 for c in r["criteria"].values())


def test_explanations_cover_every_criterion():
    res = cmp.compare(base_inputs())
    for r in res["ranking"]:
        text = " ".join(r["explanation"])
        for word in ("Price:", "Lead time:", "Coverage:", "Qualification:"):
            assert word in text


def test_weights_change_the_ranking_and_are_normalised():
    price_only = cmp.compare(base_inputs(), {"price": 1, "lead_time": 0, "coverage": 0, "qualification": 0})
    assert price_only["ranking"][0]["supplier_id"] == "s3" and price_only["weights"]["price"] == 1.0
    qual_heavy = cmp.compare(base_inputs(), {"price": 1, "lead_time": 1, "coverage": 1, "qualification": 5})
    assert qual_heavy["ranking"][-1]["supplier_id"] == "s3"
    assert sum(qual_heavy["weights"].values()) == pytest.approx(1.0)


@pytest.mark.parametrize("weights,message", [
    ({"price": -1}, "negative"), ({"price": 0, "lead_time": 0, "coverage": 0, "qualification": 0}, "positive"),
    ({"vibes": 1}, "Unknown criteria"),
])
def test_invalid_weights_are_rejected(weights, message):
    with pytest.raises(cmp.ComparisonError, match=message):
        cmp.compare(base_inputs(), weights)


def test_unverified_and_foreign_currency_quotes_are_excluded_not_converted():
    inputs = base_inputs()
    inputs["quotes"][1]["status"] = "draft"
    inputs["quotes"][2]["currency"] = "USD"
    res = cmp.compare(inputs)
    assert [r["supplier_id"] for r in res["ranking"]] == ["s1"]
    reasons = {e["supplier_id"]: e["reason"] for e in res["excluded"]}
    assert reasons == {"s2": "not_verified", "s3": "currency_mismatch"}
    assert "no conversion" in next(e for e in res["excluded"] if e["supplier_id"] == "s3")["detail"]


def test_missing_currency_and_missing_quotes():
    inputs = base_inputs()
    inputs["quotes"][0]["currency"] = None
    inputs["quotes"].pop(1)
    res = cmp.compare(inputs)
    reasons = {e["supplier_id"]: e["reason"] for e in res["excluded"]}
    assert reasons == {"s1": "currency_unknown", "s2": "no_quote"}


def test_unconfirmed_matches_are_ignored():
    inputs = base_inputs()
    inputs["quotes"][0]["lines"][0]["match_status"] = "suggested"
    res = cmp.compare(inputs)
    cell = res["items"][0]["cells"]["s1"]
    assert cell["comparable_total"] is None and cell["flags"] == ["not_quoted"]
    assert "i1" not in res["basket_item_ids"]
    assert "mandatory_lines_missing" in rank_of(res, "s1")["flags"]


def test_unit_mismatch_is_not_converted():
    inputs = base_inputs()
    inputs["quotes"][1]["lines"][0]["unit"] = "kg"
    res = cmp.compare(inputs)
    cell = res["items"][0]["cells"]["s2"]
    assert cell["comparable_total"] is None and "unit_mismatch" in cell["flags"]


def test_unit_aliases_are_equivalent():
    inputs = base_inputs()
    inputs["quotes"][1]["lines"][0]["unit"] = "Pieces"
    assert cmp.compare(inputs)["items"][0]["cells"]["s2"]["comparable_total"] == pytest.approx(20000)


def test_quantity_differences_use_unit_price_times_requested_quantity():
    inputs = base_inputs()
    inputs["quotes"][0]["lines"][0].update(quantity=1000)
    cell = cmp.compare(inputs)["items"][0]["cells"]["s1"]
    assert cell["comparable_total"] == pytest.approx(21000) and "quantity_differs" in cell["flags"]


def test_total_only_lines_are_scaled_only_when_their_quantity_is_known():
    inputs = base_inputs()
    inputs["quotes"][0]["lines"][0].update(unit_price=None, total_price=4400, quantity=1000)
    cell = cmp.compare(inputs)["items"][0]["cells"]["s1"]
    assert cell["comparable_total"] == pytest.approx(22000) and "quantity_differs" in cell["flags"]
    inputs["quotes"][0]["lines"][0].update(quantity=None)
    cell = cmp.compare(inputs)["items"][0]["cells"]["s1"]
    assert cell["comparable_total"] is None and "price_not_comparable" in cell["flags"]


def test_unknown_requested_quantity_compares_per_unit():
    res = cmp.compare(base_inputs())
    i3 = next(i for i in res["items"] if i["item_id"] == "i3")
    assert i3["cells"]["s1"]["comparable_total"] == 0 and "requested_quantity_unknown" in i3["cells"]["s1"]["flags"]
    assert i3["cells"]["s2"]["flags"] == ["not_quoted"]


def test_missing_lead_time_scores_zero_and_says_so():
    inputs = base_inputs()
    for ln in inputs["quotes"][1]["lines"]:
        ln["lead_time_days"] = None
    s2 = rank_of(cmp.compare(inputs), "s2")
    assert s2["criteria"]["lead_time"]["score"] == 0.0 and "lead_time_unknown" in s2["flags"]
    assert any("not assumed" in x for x in s2["explanation"])


def test_no_common_basket_drops_price_and_redistributes_weight():
    inputs = base_inputs()
    inputs["quotes"][1]["lines"] = [line("i3", 5, unit=None, lead=10)]
    inputs["quotes"][0]["lines"] = [line("i1", 4.2, qty=5000, lead=21)]
    inputs["quotes"].pop(2)
    res = cmp.compare(inputs)
    assert res["basket_item_ids"] == [] and "price" not in res["active_weights"]
    assert sum(res["active_weights"].values()) == pytest.approx(1.0)
    assert all(r["criteria"]["price"]["score"] is None for r in res["ranking"])
    assert any("price is not scored" in f for f in res["global_flags"])


def test_low_price_outliers_and_arithmetic_and_confidence_flags():
    inputs = base_inputs()
    inputs["quotes"][2]["lines"][0].update(unit_price=1.0)
    inputs["quotes"][0]["lines"][1].update(total_price=999, confidence=0.4)
    res = cmp.compare(inputs)
    assert "price_outlier_low" in res["items"][0]["cells"]["s3"]["flags"]
    flags = res["items"][1]["cells"]["s1"]["flags"]
    assert "arithmetic_mismatch" in flags and "low_confidence" in flags


def test_multiple_lines_for_one_item_are_not_summed():
    inputs = base_inputs()
    inputs["quotes"][0]["lines"].append(line("i1", 0.5, qty=5000))
    cell = cmp.compare(inputs)["items"][0]["cells"]["s1"]
    assert cell["comparable_total"] is None and cell["flags"] == ["multiple_lines_for_item"]


def test_terms_and_expiry_flags():
    inputs = base_inputs()
    inputs["quotes"][0].update(incoterms=None, payment_terms=None, valid_until="2026-01-01")
    flags = rank_of(cmp.compare(inputs), "s1")["flags"]
    assert {"incoterms_unknown", "payment_terms_unknown", "quote_expired"} <= set(flags)
    inputs["quotes"][0]["valid_until"] = None
    assert "validity_unknown" in rank_of(cmp.compare(inputs), "s1")["flags"]


def test_near_ties_are_called_out():
    inputs = base_inputs()
    inputs["quotes"] = [quote("s1", [line("i1", 4.0, qty=5000, lead=20)]), quote("s2", [line("i1", 4.0, qty=5000, lead=20)])]
    res = cmp.compare(inputs)
    assert res["ranking"][0]["score"] == res["ranking"][1]["score"]
    assert [r["supplier_name"] for r in res["ranking"]] == ["Kessler GmbH", "Northwind Metals"]  # stable tie-break
    assert any("within 2 points" in f for f in res["global_flags"])


def test_inputs_hash_ignores_weights_but_tracks_data():
    a = cmp.compare(base_inputs(), {"price": 1, "lead_time": 1, "coverage": 1, "qualification": 1})
    b = cmp.compare(base_inputs())
    assert a["inputs_sha256"] == b["inputs_sha256"]
    changed = base_inputs()
    changed["quotes"][0]["lines"][0]["unit_price"] = 4.21
    assert cmp.compare(changed)["inputs_sha256"] != b["inputs_sha256"]


def test_empty_comparison():
    inputs = base_inputs()
    inputs["quotes"] = []
    res = cmp.compare(inputs)
    assert res["ranking"] == [] and res["highest_score_supplier_id"] is None
    assert {e["reason"] for e in res["excluded"]} == {"no_quote"}

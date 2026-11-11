# Quote analysis: formulas and rules

Engine version 2.0 (`backend/app/analysis/engine.py`). Pure function of its inputs: same inputs and weights, same
result, bit for bit. Arithmetic uses `decimal.Decimal`; values are rounded half-even to 6 decimals only when written
to the result (money totals to 2). No AI model is involved in any step.

## Inputs

| Input | Source |
| --- | --- |
| RFQ currency, lines (quantity, unit, priority), required-by date, requested Incoterms and payment terms | RFQ |
| Verified quotes with confirmed line matches | Quotes (drafts are listed as not scored) |
| Supplier qualification at run time | Supplier qualification |
| Exchange rates `1 base = rate quote`, valid from a date | *Rates & units* |
| Unit rules `1 from = factor to` | *Rates & units* |
| Analysis date | UTC date of the run |

The SHA-256 of these inputs (weights excluded) is stored with each comparison. A comparison whose inputs no longer
match the current data is *outdated* and cannot be used for an award.

## Unit conversion

`factor(a, b)`: `1 a = factor b`. Search order, first match wins:

1. same canonical unit (aliases such as `pieces`, `Stück`, `kgs` map to `pcs`, `kg`): 1
2. same dimension: `base(a) / base(b)` with exact factors (lb = 0.45359237 kg, in = 0.0254 m, gal = 3.785411784 l, ...)
3. organization rule `a -> b`, or its inverse `1 / factor`
4. organization rule plus one standard step on either side (box -> pcs -> dozen)

No match: the line is `unit_not_convertible` and not priced. Prices per unit are divided by the factor, quantities and
MOQ are multiplied.

## Currency conversion

The rate for the pair valid on the analysis date is the one with the latest `valid_from <= analysis date`, direct
(`quote ccy -> RFQ ccy`) or inverted (`1 / rate`); on the same date a direct rate wins. Rates dated after the analysis
date are ignored. There is no triangulation. Age = analysis date - valid_from; older than `FX_MAX_AGE_DAYS` is a minor
deviation `fx_rate_stale`. No rate: the whole quote is excluded with reason `no_fx_rate`.

## Per line and supplier

```
unit_price_q   = stated unit price, else total / quoted quantity (only when the quoted quantity is stated)
price          = unit_price_q / unit_factor * fx_rate                 # per requested unit, RFQ currency
moq_req        = moq * unit_factor
payable_qty    = max(requested_qty, moq_req)                          # requested_qty if no MOQ
requested_total= price * requested_qty
comparable     = price * payable_qty
moq_excess     = comparable - requested_total                         # when payable_qty > requested_qty
```

When the RFQ line has no quantity, `comparable = price` (compared per unit for every supplier alike). Several confirmed
lines for one RFQ line are never summed (`multiple_lines_for_item`). A price below `median * (1 - COMPARISON_OUTLIER_RATIO)`
of at least three prices is `price_outlier_low`.

## Supplier criteria (each 0..1)

| Criterion | Formula |
| --- | --- |
| Price | `lowest basket / supplier basket`; basket = sum of `comparable` over lines every scored supplier priced. No such line: not scored and its weight is spread over the others. |
| Lead time | `fastest / supplier lead time`, lead time = longest priced line. Not stated: 0. |
| Coverage | priority-weighted share of lines with a comparable price (mandatory 2, preferred 1, optional 0.5, unknown 1) |
| Qualification | 1 if currently qualified, else 0 |
| Risk | `1 - risk points / 100` |

Score = `100 * sum(weight_k * criterion_k) / sum(active weights)`. Default weights: price 0.45, lead time 0.20,
coverage 0.15, qualification 0.10, risk 0.10. Ties are ordered by supplier name; scores within 2 points are flagged.

## Lead time

`slack = (required_by - analysis date) - lead time`; `on_time = slack >= 0`. Late delivery is a major deviation.

## Deviations and risk

| Code | Severity |
| --- | --- |
| `supplier_not_qualified`, `quote_expired` | critical |
| `line_not_quoted` | critical (mandatory line), major (preferred), minor (other) |
| `incoterms_differ`, `lead_time_exceeds_required`, `multiple_lines_for_item`, `unit_not_convertible`, `currency_not_convertible`, `arithmetic_mismatch` (> 1 %), `price_outlier_low` | major |
| `moq_above_quantity` | major if the extra cost exceeds 10 % of the requested total, else minor |
| `price_not_stated`, `price_not_comparable` | major on mandatory lines, else minor |
| `payment_terms_differ`, `incoterms_not_stated`, `payment_terms_not_stated`, `validity_not_stated`, `validity_ends_soon` (`QUOTE_VALIDITY_WARNING_DAYS`), `fx_rate_stale`, `quantity_differs`, `unit_not_stated`, `low_confidence` (< 0.6), `lead_time_not_stated` | minor |
| `currency_converted`, `unit_converted`, `extra_line` | info |

Risk points = `25 * critical + 10 * major + 3 * minor`, capped at 100. Level: high >= 50, medium >= 20, low otherwise.

## Spreadsheet ingestion

Header row: the first of the first 10 rows that names a description column and at least one other known column.
CSV delimiter: the most frequent of `, ; TAB |` in the first non-empty line. Decimal separator: taken from the file's
unambiguous numbers (both separators present, a repeated separator, or a separator not followed by exactly three
digits); a number such as `1.000` or `1,000` is read with that convention and left empty with a warning when the file
gives none or gives conflicting evidence. Currency: ISO codes in a currency column, in price cells, or in a header such
as `Unit price (EUR)`; several currencies leave it to the person. At most 500 lines.

## Award

`recommend` (comparisons:run): RFQ sent or closed, comparison current, supplier ranked in it, rationale >= 10 characters,
one pending recommendation per RFQ. `approve` (rfqs:approve): not the recommender, not the RFQ creator, comparison still
current, supplier still qualified, note required when the supplier was not the top score. Approval sets the RFQ to
`awarded` with supplier, approver, comparison, recommendation and combined rationale.

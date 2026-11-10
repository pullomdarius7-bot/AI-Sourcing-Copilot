"""Deterministic quote analysis. Pure modules: no database, no settings, no AI.

units     unit canonicalisation and conversion factors (built-in + organization-defined)
fx        currency conversion from organization-maintained exchange rates
ingest    parsing supplier quote spreadsheets (CSV / XLSX) into quote lines
engine    normalization, MOQ and lead-time analysis, deviation detection, risk and scoring

All arithmetic uses decimal.Decimal so the same inputs always give the same outputs.
"""

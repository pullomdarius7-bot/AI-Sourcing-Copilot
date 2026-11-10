"""Structured-output contract for document analysis.

Follows the product's extraction rules: every value is {value, unknown, confidence, source, verified};
unknown is explicit, nothing is assumed, and `verified` is only ever set by a human, never by a model.
"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DocumentType = Literal[
    "supplier_quote", "rfq", "specification", "contract", "invoice", "other"
]
INCOTERMS = {"EXW", "FCA", "CPT", "CIP", "DAP", "DPU", "DDP", "FAS", "FOB", "CFR", "CIF"}


class StrField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str | None = Field(default=None, max_length=500)
    unknown: bool = True
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: str | None = Field(default=None, max_length=600)
    verified: bool = False


class NumField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float | None = Field(default=None, ge=0)
    unknown: bool = True
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: str | None = Field(default=None, max_length=600)
    verified: bool = False


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=500)
    source: str = Field(min_length=1, max_length=800)
    quantity: NumField
    unit: StrField
    unit_price: NumField
    total_price: NumField
    lead_time_days: NumField


class Analysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: DocumentType
    summary: str = Field(max_length=1000)
    supplier_name: StrField
    currency: StrField
    incoterms: StrField
    payment_terms: StrField
    valid_until: StrField
    line_items: list[LineItem] = Field(max_length=200)
    overall_confidence: float = Field(ge=0.0, le=1.0)


def _field(value_type: str) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["value", "unknown", "confidence", "source"],
        "properties": {
            "value": {"type": [value_type, "null"]},
            "unknown": {"type": "boolean"},
            "confidence": {"type": "number"},
            "source": {"type": ["string", "null"]},
        },
    }


_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "description", "source", "quantity", "unit", "unit_price", "total_price", "lead_time_days",
    ],
    "properties": {
        "description": {"type": "string"},
        "source": {"type": "string"},
        "quantity": _field("number"),
        "unit": _field("string"),
        "unit_price": _field("number"),
        "total_price": _field("number"),
        "lead_time_days": _field("number"),
    },
}

ANALYSIS_SCHEMA_NAME = "record_analysis"
ANALYSIS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "document_type", "summary", "supplier_name", "currency", "incoterms", "payment_terms",
        "valid_until", "line_items", "overall_confidence",
    ],
    "properties": {
        "document_type": {"type": "string", "enum": list(DocumentType.__args__)},  # type: ignore[attr-defined]
        "summary": {"type": "string"},
        "supplier_name": _field("string"),
        "currency": _field("string"),
        "incoterms": _field("string"),
        "payment_terms": _field("string"),
        "valid_until": _field("string"),
        "line_items": {"type": "array", "items": _ITEM},
        "overall_confidence": {"type": "number"},
    },
}

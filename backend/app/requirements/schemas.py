"""Structured-output contract for requirement extraction (what the model is asked to return)."""
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai.guardrails import OutputInvalid

CATEGORIES = [
    "material", "dimensions", "tolerance", "quantity", "delivery", "quality", "compliance",
    "commercial", "packaging", "other",
]
PRIORITIES = ["mandatory", "preferred", "optional", "unknown"]
QUALIFIERS = ["exact", "min", "max", "approx"]
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,59}$")

Category = Literal[
    "material", "dimensions", "tolerance", "quantity", "delivery", "quality", "compliance",
    "commercial", "packaging", "other",
]
Priority = Literal["mandatory", "preferred", "optional", "unknown"]
Qualifier = Literal["exact", "min", "max", "approx"]

EXTRACTION_SCHEMA_NAME = "record_requirements"


class RawParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=NAME_RE.pattern)
    value_type: Literal["text", "number"]
    text: str | None = Field(default=None, max_length=500)
    number: float | None = None
    unit: str | None = Field(default=None, max_length=30)
    qualifier: Qualifier | None = None
    unknown: bool
    confidence: float = Field(ge=0.0, le=1.0)
    source: str | None = Field(default=None, max_length=1200)


class RawRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=600)
    source: str = Field(min_length=1, max_length=1200)
    category: Category
    priority: Priority
    confidence: float = Field(ge=0.0, le=1.0)
    parameters: list[RawParameter] = Field(max_length=30)


class RawUnspecified(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=NAME_RE.pattern)
    reason: str = Field(max_length=300)


class RawExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirements: list[RawRequirement] = Field(max_length=100)
    unspecified: list[RawUnspecified] = Field(max_length=40)


def validate_raw(raw: Any) -> RawExtraction:
    if not isinstance(raw, dict):
        raise OutputInvalid("The answer must be a JSON object")
    try:
        return RawExtraction.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:8])
        raise OutputInvalid(problems) from exc


def _nullable(kind: str) -> dict[str, Any]:
    return {"type": [kind, "null"]}


_PARAMETER = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "value_type", "text", "number", "unit", "qualifier", "unknown", "confidence", "source"],
    "properties": {
        "name": {"type": "string"},
        "value_type": {"type": "string", "enum": ["text", "number"]},
        "text": _nullable("string"),
        "number": _nullable("number"),
        "unit": _nullable("string"),
        "qualifier": {"type": ["string", "null"], "enum": [*QUALIFIERS, None]},
        "unknown": {"type": "boolean"},
        "confidence": {"type": "number"},
        "source": _nullable("string"),
    },
}

EXTRACTION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["requirements", "unspecified"],
    "properties": {
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["statement", "source", "category", "priority", "confidence", "parameters"],
                "properties": {
                    "statement": {"type": "string"},
                    "source": {"type": "string"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "priority": {"type": "string", "enum": PRIORITIES},
                    "confidence": {"type": "number"},
                    "parameters": {"type": "array", "items": _PARAMETER},
                },
            },
        },
        "unspecified": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "reason"],
                "properties": {"name": {"type": "string"}, "reason": {"type": "string"}},
            },
        },
    },
}

"""Quote comparison: delegates to the deterministic analysis engine (app.analysis.engine).

Kept as the module the RFQ service and router call, so callers and stored comparisons keep one entry point.
"""
from typing import Any

from app.analysis.engine import DEFAULT_WEIGHTS, ENGINE_VERSION, analyse, inputs_hash, normalise_weights
from app.analysis.engine import AnalysisError as ComparisonError

__all__ = ["DEFAULT_WEIGHTS", "ENGINE_VERSION", "ComparisonError", "compare", "inputs_hash", "normalise_weights"]


def compare(inputs: dict[str, Any], weights: dict[str, float] | None = None, outlier_ratio: float = 0.5,
            **params: Any) -> dict[str, Any]:
    return analyse(inputs, weights, outlier_ratio=outlier_ratio, **params)

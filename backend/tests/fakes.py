from app.ai.providers import LLMProvider, LLMRequest, LLMResponse, ProviderError
from tests.samples import QUOTE


def _f(value, source, confidence=0.9):
    return {"value": value, "unknown": False, "confidence": confidence, "source": source}


def _unknown():
    return {"value": None, "unknown": True, "confidence": 0.0, "source": None}


ITEM_LINE = "Item: Steel bracket M8 | Qty 100 | Unit price 4.20 | Total 420.00 | Lead time 21 days"


def good_analysis(**overrides):
    data = {
        "document_type": "supplier_quote",
        "summary": "Quote for steel brackets from Northwind Metals.",
        "supplier_name": _f("Northwind Metals GmbH", "Supplier: Northwind Metals GmbH"),
        "currency": _f("EUR", "Currency: EUR"),
        "incoterms": _f("FOB", "Incoterms: FOB Hamburg"),
        "payment_terms": _f("Net 30", "Payment terms: Net 30"),
        "valid_until": _f("2026-12-31", "Valid until: 2026-12-31"),
        "line_items": [
            {
                "description": "Steel bracket M8",
                "source": ITEM_LINE,
                "quantity": _f(100, "Qty 100"),
                "unit": _unknown(),
                "unit_price": _f(4.2, "Unit price 4.20"),
                "total_price": _f(420, "Total 420.00"),
                "lead_time_days": _f(21, "Lead time 21 days"),
            }
        ],
        "overall_confidence": 0.9,
    }
    data.update(overrides)
    return data


class FakeProvider(LLMProvider):
    """Plays back a script. Each step is a dict (answer), an Exception (raised) or a callable(request)."""

    def __init__(self, script, name="fake", external=True, model="fake-1"):
        super().__init__(model)
        self.name = name
        self.external = external
        self.script = list(script)
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if callable(step):
            step = step(request)
        if isinstance(step, Exception):
            raise step
        return LLMResponse(step, self.model, 120, 80)


def transient(msg="boom") -> ProviderError:
    return ProviderError(msg, retryable=True)

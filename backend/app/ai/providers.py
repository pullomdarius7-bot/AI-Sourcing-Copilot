"""LLM provider abstraction: Anthropic, OpenAI (and OpenAI-compatible servers), local LLM via Ollama."""
import base64
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.ai.schemas import ANALYSIS_SCHEMA_NAME
from app.config import Settings, get_settings

settings = get_settings()


class ProviderError(Exception):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass
class LLMRequest:
    system: str
    user: str
    schema: dict[str, Any]
    images: list[tuple[str, bytes]] = field(default_factory=list)  # (mime type, bytes)
    max_tokens: int = 4096
    schema_name: str = ANALYSIS_SCHEMA_NAME


@dataclass
class LLMResponse:
    data: Any
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMProvider:
    name = "base"
    external = True

    def __init__(self, model: str, client: httpx.AsyncClient | None = None) -> None:
        self.model = model
        self._client = client

    async def generate(self, request: LLMRequest) -> LLMResponse:  # pragma: no cover - interface
        raise NotImplementedError

    async def _post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        owns = self._client is None
        client = self._client or httpx.AsyncClient(timeout=settings.ai_timeout_seconds)
        try:
            response = await client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise ProviderError(f"{self.name} request timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} unreachable: {type(exc).__name__}", retryable=True) from exc
        finally:
            if owns:
                await client.aclose()
        if response.status_code in (408, 409, 425, 429) or response.status_code >= 500:
            raise ProviderError(f"{self.name} returned HTTP {response.status_code}", retryable=True)
        if response.status_code >= 400:
            raise ProviderError(
                f"{self.name} rejected the request (HTTP {response.status_code}): {response.text[:200]}",
                retryable=False,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(f"{self.name} returned a non-JSON response", retryable=True) from exc


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    external = True

    def __init__(self, api_key: str, model: str, base_url: str, client: httpx.AsyncClient | None = None):
        super().__init__(model, client)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def generate(self, request: LLMRequest) -> LLMResponse:
        content: list[dict[str, Any]] = [
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": _b64(data)}}
            for mime, data in request.images
        ]
        content.append({"type": "text", "text": request.user})
        body = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "temperature": 0,
            "system": request.system,
            "messages": [{"role": "user", "content": content}],
            "tools": [{
                "name": request.schema_name,
                "description": "Record the structured result for the document.",
                "input_schema": request.schema,
            }],
            "tool_choice": {"type": "tool", "name": request.schema_name},
        }
        data = await self._post(
            f"{self.base_url}/v1/messages",
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            body,
        )
        if data.get("stop_reason") == "max_tokens":
            raise ProviderError("anthropic output was truncated", retryable=False)
        for block in data.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == request.schema_name:
                usage = data.get("usage", {})
                return LLMResponse(
                    block.get("input"), data.get("model", self.model),
                    usage.get("input_tokens"), usage.get("output_tokens"),
                )
        raise ProviderError("anthropic returned no structured output", retryable=True)


class OpenAIProvider(LLMProvider):
    name = "openai"
    external = True

    def __init__(self, api_key: str, model: str, base_url: str, client: httpx.AsyncClient | None = None):
        super().__init__(model, client)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def generate(self, request: LLMRequest) -> LLMResponse:
        user_content: Any = request.user
        if request.images:
            user_content = [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{_b64(data)}"}}
                for mime, data in request.images
            ] + [{"type": "text", "text": request.user}]
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": request.max_tokens,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": user_content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": request.schema_name, "strict": True, "schema": request.schema},
            },
        }
        data = await self._post(
            f"{self.base_url}/chat/completions",
            {"authorization": f"Bearer {self.api_key}", "content-type": "application/json"},
            body,
        )
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("openai returned an unexpected response", retryable=True) from exc
        if message.get("refusal"):
            raise ProviderError("openai refused the request", retryable=False)
        if choice.get("finish_reason") == "length":
            raise ProviderError("openai output was truncated", retryable=False)
        import json

        try:
            parsed = json.loads(message.get("content") or "")
        except ValueError as exc:
            raise ProviderError("openai returned invalid JSON", retryable=True) from exc
        usage = data.get("usage", {})
        return LLMResponse(
            parsed, data.get("model", self.model), usage.get("prompt_tokens"), usage.get("completion_tokens")
        )


class LocalProvider(LLMProvider):
    """Self-hosted model served by Ollama. Document text never leaves your network."""

    name = "local"
    external = False

    def __init__(self, base_url: str, model: str, client: httpx.AsyncClient | None = None):
        super().__init__(model, client)
        self.base_url = base_url.rstrip("/")

    async def generate(self, request: LLMRequest) -> LLMResponse:
        user: dict[str, Any] = {"role": "user", "content": request.user}
        if request.images:
            user["images"] = [_b64(data) for _, data in request.images]
        body = {
            "model": self.model,
            "stream": False,
            "format": request.schema,
            "options": {"temperature": 0, "num_predict": request.max_tokens},
            "messages": [{"role": "system", "content": request.system}, user],
        }
        data = await self._post(f"{self.base_url}/api/chat", {"content-type": "application/json"}, body)
        import json

        try:
            parsed = json.loads(data["message"]["content"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError("local model returned invalid JSON", retryable=True) from exc
        return LLMResponse(parsed, data.get("model", self.model), data.get("prompt_eval_count"), data.get("eval_count"))


def build_providers(cfg: Settings | None = None, client: httpx.AsyncClient | None = None) -> list[LLMProvider]:
    cfg = cfg or get_settings()
    providers: list[LLMProvider] = []
    for name in (n.strip().lower() for n in cfg.ai_provider_order.split(",") if n.strip()):
        if name == "anthropic" and cfg.anthropic_api_key:
            providers.append(AnthropicProvider(cfg.anthropic_api_key, cfg.anthropic_model, cfg.anthropic_base_url, client))
        elif name == "openai" and cfg.openai_api_key:
            providers.append(OpenAIProvider(cfg.openai_api_key, cfg.openai_model, cfg.openai_base_url, client))
        elif name == "local" and cfg.local_llm_url:
            providers.append(LocalProvider(cfg.local_llm_url, cfg.local_llm_model, client))
    return providers

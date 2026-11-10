import json

import httpx
import pytest

from app.ai.providers import (
    AnthropicProvider,
    LLMRequest,
    LocalProvider,
    OpenAIProvider,
    ProviderError,
    build_providers,
)
from app.ai.schemas import ANALYSIS_JSON_SCHEMA
from app.config import get_settings
from tests.fakes import good_analysis

REQ = LLMRequest("system rules", "document text", ANALYSIS_JSON_SCHEMA)


def client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_anthropic_forces_tool_use_and_parses_result():
    seen = {}

    def handler(request):
        seen["url"], seen["headers"], seen["body"] = str(request.url), request.headers, json.loads(request.content)
        return httpx.Response(200, json={
            "model": "claude-test", "stop_reason": "tool_use", "usage": {"input_tokens": 11, "output_tokens": 7},
            "content": [{"type": "text", "text": "ok"}, {"type": "tool_use", "name": "record_analysis", "input": good_analysis()}],
        })

    provider = AnthropicProvider("sk-ant-secret", "claude-test", "https://api.anthropic.com", client_for(handler))
    out = await provider.generate(LLMRequest("sys", "doc", ANALYSIS_JSON_SCHEMA, [("image/png", b"\x89PNG")]))
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-ant-secret" and seen["headers"]["anthropic-version"]
    assert seen["body"]["tool_choice"] == {"type": "tool", "name": "record_analysis"}
    assert seen["body"]["tools"][0]["input_schema"] == ANALYSIS_JSON_SCHEMA and seen["body"]["temperature"] == 0
    assert seen["body"]["messages"][0]["content"][0]["type"] == "image"
    assert (out.data["document_type"], out.model, out.input_tokens, out.output_tokens) == ("supplier_quote", "claude-test", 11, 7)


async def test_anthropic_without_tool_use_is_retryable():
    provider = AnthropicProvider("k", "m", "https://x", client_for(lambda r: httpx.Response(200, json={"content": [{"type": "text", "text": "hi"}]})))
    with pytest.raises(ProviderError) as err:
        await provider.generate(REQ)
    assert err.value.retryable


async def test_anthropic_truncated_output_is_not_retried():
    provider = AnthropicProvider("k", "m", "https://x", client_for(lambda r: httpx.Response(200, json={"stop_reason": "max_tokens", "content": []})))
    with pytest.raises(ProviderError) as err:
        await provider.generate(REQ)
    assert not err.value.retryable


async def test_openai_uses_strict_json_schema_and_parses():
    seen = {}

    def handler(request):
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers["authorization"], json.loads(request.content)
        return httpx.Response(200, json={
            "model": "gpt-test", "usage": {"prompt_tokens": 20, "completion_tokens": 9},
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(good_analysis())}}],
        })

    provider = OpenAIProvider("sk-secret", "gpt-test", "https://api.openai.com/v1", client_for(handler))
    out = await provider.generate(LLMRequest("sys", "doc", ANALYSIS_JSON_SCHEMA, [("image/jpeg", b"\xff\xd8\xff")]))
    assert seen["url"] == "https://api.openai.com/v1/chat/completions" and seen["auth"] == "Bearer sk-secret"
    rf = seen["body"]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True and rf["json_schema"]["schema"] == ANALYSIS_JSON_SCHEMA
    assert seen["body"]["messages"][1]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert out.data["document_type"] == "supplier_quote" and out.output_tokens == 9


async def test_openai_refusal_and_bad_json():
    refuse = OpenAIProvider("k", "m", "https://x", client_for(lambda r: httpx.Response(200, json={"choices": [{"message": {"refusal": "no"}}]})))
    with pytest.raises(ProviderError) as err:
        await refuse.generate(REQ)
    assert not err.value.retryable
    junk = OpenAIProvider("k", "m", "https://x", client_for(lambda r: httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "not json"}}]})))
    with pytest.raises(ProviderError) as err:
        await junk.generate(REQ)
    assert err.value.retryable


async def test_local_provider_sends_schema_as_format():
    seen = {}

    def handler(request):
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return httpx.Response(200, json={"model": "llama", "message": {"content": json.dumps(good_analysis())}, "prompt_eval_count": 50, "eval_count": 30})

    provider = LocalProvider("http://ollama:11434", "llama", client_for(handler))
    out = await provider.generate(LLMRequest("sys", "doc", ANALYSIS_JSON_SCHEMA, [("image/png", b"abc")]))
    assert seen["url"] == "http://ollama:11434/api/chat" and seen["body"]["format"] == ANALYSIS_JSON_SCHEMA
    assert seen["body"]["stream"] is False and seen["body"]["messages"][1]["images"] == ["YWJj"]
    assert not provider.external and out.input_tokens == 50


@pytest.mark.parametrize("status,retryable", [(429, True), (500, True), (503, True), (408, True), (400, False), (401, False), (403, False)])
async def test_http_errors_are_classified(status, retryable):
    provider = OpenAIProvider("k", "m", "https://x", client_for(lambda r: httpx.Response(status, text="nope")))
    with pytest.raises(ProviderError) as err:
        await provider.generate(REQ)
    assert err.value.retryable is retryable


@pytest.mark.parametrize("exc", [httpx.ReadTimeout("slow"), httpx.ConnectError("refused")])
async def test_network_failures_are_retryable(exc):
    def handler(request):
        raise exc

    provider = LocalProvider("http://ollama:11434", "m", client_for(handler))
    with pytest.raises(ProviderError) as err:
        await provider.generate(REQ)
    assert err.value.retryable


async def test_errors_never_leak_api_keys():
    provider = AnthropicProvider("sk-ant-supersecret", "m", "https://x", client_for(lambda r: httpx.Response(401, text="invalid key")))
    with pytest.raises(ProviderError) as err:
        await provider.generate(REQ)
    assert "supersecret" not in str(err.value)


def test_build_providers_follows_order_and_configuration():
    cfg = get_settings().model_copy(update={
        "ai_provider_order": "openai, local ,anthropic,unknown", "openai_api_key": "k1",
        "local_llm_url": "http://ollama:11434", "anthropic_api_key": "",
    })
    assert [p.name for p in build_providers(cfg)] == ["openai", "local"]
    cfg2 = cfg.model_copy(update={"openai_api_key": "", "local_llm_url": "", "anthropic_api_key": ""})
    assert build_providers(cfg2) == []
    assert [p.external for p in build_providers(cfg)] == [True, False]

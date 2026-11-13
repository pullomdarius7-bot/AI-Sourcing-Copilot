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

settings = get_settings()

REQ = LLMRequest("system rules", "document text", ANALYSIS_JSON_SCHEMA)


def client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


SMALL_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["name", "kind"],
    "properties": {"name": {"type": ["string", "null"]}, "kind": {"type": "string", "enum": ["a", "b"]}},
}


async def test_anthropic_asks_for_json_output_and_parses_it():
    seen = {}

    def handler(request):
        seen["url"], seen["headers"], seen["body"] = str(request.url), request.headers, json.loads(request.content)
        return httpx.Response(200, json={
            "model": "claude-test", "stop_reason": "end_turn", "usage": {"input_tokens": 11, "output_tokens": 7},
            "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": json.dumps({"name": None, "kind": "a"})}],
        })

    provider = AnthropicProvider("sk-ant-secret", "claude-test", "https://api.anthropic.com", client_for(handler))
    out = await provider.generate(LLMRequest("sys", "doc", SMALL_SCHEMA, schema_name="record_thing"))
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-ant-secret" and seen["headers"]["anthropic-version"]
    body = seen["body"]
    # Current models reject a forced tool call and non-default sampling parameters.
    assert not {"tool_choice", "tools", "temperature", "top_p", "top_k"} & set(body)
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["effort"] == settings.anthropic_effort
    assert out.data == {"name": None, "kind": "a"} and out.input_tokens == 11 and out.output_tokens == 7


async def test_a_schema_too_complex_to_compile_goes_through_a_tool_call():
    """The API caps strict schemas at 16 fields of several types; document analysis has more."""
    from app.ai.providers import MAX_STRICT_UNIONS, json_output_schema, union_count

    assert union_count(json_output_schema(ANALYSIS_JSON_SCHEMA)) > MAX_STRICT_UNIONS
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "claude-test", "stop_reason": "tool_use", "usage": {"input_tokens": 5, "output_tokens": 9},
            "content": [{"type": "text", "text": "Recording."},
                        {"type": "tool_use", "id": "t1", "name": "record_analysis", "input": good_analysis()}],
        })

    provider = AnthropicProvider("k", "claude-test", "https://x", client_for(handler))
    out = await provider.generate(REQ)
    body = seen["body"]
    assert body["tool_choice"] == {"type": "auto"}  # forced tool choice is rejected by current models
    assert body["tools"][0]["name"] == "record_analysis" and body["tools"][0]["input_schema"] == ANALYSIS_JSON_SCHEMA
    assert "format" not in body["output_config"] and "record_analysis tool" in body["system"]
    assert "temperature" not in body
    assert out.data == good_analysis()


def test_every_schema_in_the_app_either_compiles_strictly_or_uses_the_tool_path():
    from app.agent.prompts import ANSWER_SCHEMA, PLAN_SCHEMA
    from app.ai.providers import MAX_STRICT_UNIONS, json_output_schema, union_count
    from app.marketplace.manifest import NORMALIZE_SCHEMA, PDF_SCHEMA
    from app.marketplace.service import VALUATION_SCHEMA
    from app.requirements.schemas import EXTRACTION_JSON_SCHEMA
    from app.sourcing import extract

    strict = {name for name, schema in {
        "extraction": EXTRACTION_JSON_SCHEMA, "normalize": NORMALIZE_SCHEMA, "pdf": PDF_SCHEMA,
        "valuation": VALUATION_SCHEMA, "plan": PLAN_SCHEMA, "answer": ANSWER_SCHEMA, "supplier": extract.SCHEMA,
        "analysis": ANALYSIS_JSON_SCHEMA,
    }.items() if union_count(json_output_schema(schema)) <= MAX_STRICT_UNIONS}
    assert strict == {"extraction", "normalize", "pdf", "valuation", "plan", "answer", "supplier"}


def test_json_output_schema_keeps_the_shape_and_drops_what_the_api_rejects():
    from app.ai.providers import json_output_schema

    source = {
        "type": "object", "required": ["name", "items"],
        "properties": {
            "name": {"type": ["string", "null"], "maxLength": 50},
            "maxItems": {"type": "number", "minimum": 0},  # a property that happens to be named like a keyword
            "items": {"type": "array", "maxItems": 5, "items": {"type": "object", "properties": {"n": {"type": "integer"}}}},
        },
    }
    out = json_output_schema(source)
    assert out["additionalProperties"] is False and out["required"] == ["name", "items"]
    assert out["properties"]["name"] == {"anyOf": [{"type": "string"}, {"type": "null"}]}
    assert out["properties"]["maxItems"] == {"type": "number"}
    assert out["properties"]["items"] == {
        "type": "array", "items": {"type": "object", "properties": {"n": {"type": "integer"}}, "additionalProperties": False}}
    assert "maxLength" in json.dumps(source)  # the caller's schema is not modified

    def keywords(node, found):
        if isinstance(node, dict):
            for key, value in node.items():
                found.add(key)
                keywords(list(value.values()) if key == "properties" else value, found)
        elif isinstance(node, list):
            for item in node:
                keywords(item, found)
        return found

    from app.agent.prompts import ANSWER_SCHEMA, PLAN_SCHEMA
    from app.marketplace.manifest import NORMALIZE_SCHEMA, PDF_SCHEMA
    from app.marketplace.service import VALUATION_SCHEMA
    from app.requirements.schemas import EXTRACTION_JSON_SCHEMA
    from app.sourcing import extract

    allowed = {"type", "properties", "required", "additionalProperties", "items", "enum", "anyOf", "description"}
    python_types = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "null": type(None)}

    def check(node, where):
        """What the API checks: one declared type per node, and every enum value of that type."""
        if isinstance(node, dict):
            if "type" in node:
                assert isinstance(node["type"], str), f"{where}: a list of types must become anyOf"
            if "enum" in node:
                assert node["enum"], f"{where}: empty enum"
                for value in node["enum"]:
                    assert isinstance(value, python_types[node["type"]]), f"{where}: enum value {value!r}"
            for key, value in node.items():
                if key == "properties":
                    for name, sub in value.items():
                        check(sub, f"{where}.{name}")
                elif key != "enum":
                    check(value, where)
        elif isinstance(node, list):
            for item in node:
                check(item, where)

    for schema in (ANALYSIS_JSON_SCHEMA, EXTRACTION_JSON_SCHEMA, NORMALIZE_SCHEMA, PDF_SCHEMA, VALUATION_SCHEMA,
                   PLAN_SCHEMA, ANSWER_SCHEMA, extract.SCHEMA):
        converted = json_output_schema(schema)
        assert keywords(converted, set()) <= allowed
        check(converted, "schema")


def test_a_nullable_enum_keeps_null_out_of_the_string_choices():
    from app.ai.providers import json_output_schema

    out = json_output_schema({"type": ["string", "null"], "enum": ["new", "used", None]})
    assert out == {"anyOf": [{"type": "string", "enum": ["new", "used"]}, {"type": "null"}]}


async def test_anthropic_without_json_is_retryable():
    provider = AnthropicProvider("k", "m", "https://x", client_for(lambda r: httpx.Response(200, json={"content": [{"type": "text", "text": "hi"}]})))
    with pytest.raises(ProviderError) as err:
        await provider.generate(REQ)
    assert err.value.retryable


async def test_anthropic_refusal_is_not_retried():
    provider = AnthropicProvider("k", "m", "https://x", client_for(lambda r: httpx.Response(200, json={"stop_reason": "refusal", "content": []})))
    with pytest.raises(ProviderError) as err:
        await provider.generate(REQ)
    assert not err.value.retryable and "declined" in str(err.value)


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
    # Ollama's default context (2048 tokens) would silently cut documents short; slow CPUs get a longer timeout.
    assert seen["body"]["options"]["num_ctx"] == settings.local_llm_num_ctx
    assert seen["body"]["options"]["num_predict"] <= settings.local_llm_max_output_tokens
    assert provider.timeout == settings.local_llm_timeout_seconds


def test_a_local_model_caps_the_document_text_for_every_provider():
    from app.ai.service import input_limit

    local = LocalProvider("http://ollama:11434", "m")
    hosted = OpenAIProvider("k", "m", "https://x")
    assert input_limit([hosted]) == settings.ai_max_input_chars
    assert input_limit([local, hosted]) == min(settings.ai_max_input_chars, settings.local_llm_max_input_chars)


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


async def test_a_server_error_carries_the_servers_own_reason():
    reply = httpx.Response(500, json={"error": "model requires more system memory (6.1 GiB) than is available (4.2 GiB)"})
    provider = LocalProvider("http://ollama:11434", "m", client_for(lambda r: reply))
    with pytest.raises(ProviderError) as err:
        await provider.generate(REQ)
    assert err.value.retryable and "HTTP 500" in str(err.value) and "more system memory" in str(err.value)

import json

import httpx
import pytest

from app.core.config import Settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import LlmError
from app.providers.llm.openai_compatible import OpenAICompatibleLlmProvider


def test_loads_json_object_extracts_first_object_from_mixed_text():
    content = (
        "I'll explain briefly.\n"
        '{"endpoints": [{"path": "/health"}], "warnings": []}\n'
        "Extra explanation that should be ignored."
    )

    payload = OpenAICompatibleLlmProvider._loads_json_object(content)

    assert payload["endpoints"][0]["path"] == "/health"


def test_loads_json_object_rejects_non_object_json():
    with pytest.raises(ValueError):
        OpenAICompatibleLlmProvider._loads_json_object('[{"path": "/health"}]')


def test_extract_first_json_handles_braces_inside_strings():
    content = 'prefix {"message": "literal } brace", "ok": true} suffix'

    extracted = OpenAICompatibleLlmProvider._extract_first_json(content)

    assert extracted == '{"message": "literal } brace", "ok": true}'


def _provider(monkeypatch, handler, **settings_overrides):
    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=transport, **kwargs),
    )
    return OpenAICompatibleLlmProvider(
        Settings(
            llm_api_key="test-key",
            llm_model="deepseek-flash",
            log_llm_content=False,
            **settings_overrides,
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url,deepseek",
    [("https://api.deepseek.com", True), ("https://api.openai.com/v1", False)],
)
async def test_generate_json_uses_provider_specific_thinking_toggle(
    monkeypatch, base_url, deepseek
):
    def handler(request):
        payload = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer test-key"
        assert payload["response_format"] == {"type": "json_object"}
        if deepseek:
            assert payload["thinking"] == {"type": "disabled"}
            assert payload["max_tokens"] == 8192
        else:
            assert "thinking" not in payload
            assert "max_tokens" not in payload
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"ok": true}'}}]}
        )

    provider = _provider(monkeypatch, handler, llm_base_url=base_url)
    assert await provider.generate_json(system_prompt="Return JSON", user_prompt="ok") == {
        "ok": True
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [None, "", "   ", [], {}])
async def test_generate_json_rejects_empty_or_non_text_content(monkeypatch, content):
    provider = _provider(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        ),
    )
    with pytest.raises(LlmError) as caught:
        await provider.generate_json(system_prompt="JSON", user_prompt="ok")
    assert caught.value.code == ErrorCode.LLM_NO_RESPONSE.code


@pytest.mark.asyncio
async def test_generate_json_wraps_non_json_http_response(monkeypatch):
    provider = _provider(
        monkeypatch, lambda request: httpx.Response(200, text="upstream HTML error")
    )
    with pytest.raises(LlmError) as caught:
        await provider.generate_json(system_prompt="JSON", user_prompt="ok")
    assert caught.value.code == ErrorCode.LLM_NO_RESPONSE.code


@pytest.mark.asyncio
async def test_generate_json_rejects_truncated_but_parseable_json(monkeypatch):
    provider = _provider(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "{}"}, "finish_reason": "length"}
                ]
            },
        ),
    )
    with pytest.raises(LlmError) as caught:
        await provider.generate_json(system_prompt="JSON", user_prompt="ok")
    assert caught.value.code == ErrorCode.LLM_INVALID_RESPONSE.code


@pytest.mark.asyncio
async def test_generate_json_does_not_expose_response_when_content_logging_disabled(monkeypatch):
    private_content = "private-contract-data-is-not-json"
    provider = _provider(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": private_content}}]}
        ),
    )
    with pytest.raises(LlmError) as caught:
        await provider.generate_json(system_prompt="JSON", user_prompt="ok")
    assert caught.value.code == ErrorCode.LLM_INVALID_RESPONSE.code
    assert private_content not in str(caught.value.context)
    assert "raw_response_excerpt" not in caught.value.context


@pytest.mark.asyncio
async def test_generate_json_http_error_does_not_store_upstream_body(monkeypatch):
    provider = _provider(
        monkeypatch,
        lambda request: httpx.Response(401, text="private-key-or-provider-diagnostics"),
    )
    with pytest.raises(LlmError) as caught:
        await provider.generate_json(system_prompt="JSON", user_prompt="ok")
    assert caught.value.code == ErrorCode.LLM_AUTH_ERROR.code
    assert "private-key" not in str(caught.value)
    assert "private-key" not in str(caught.value.context)

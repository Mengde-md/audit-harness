import contextlib
import json
import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest

from app.mock_llm.env_detector import build_mock_llm_environment
from app.mock_llm.gateway import MockLlmHandler
from app.mock_llm.responses import MockResponseEngine
from app.schemas.mock_llm import LlmMockConfig


def test_env_detector_overrides_known_and_project_llm_variables(tmp_path):
    (tmp_path / ".env").write_text(
        "CUSTOM_LLM_BASE_URL=https://real.example/v1\n"
        "CUSTOM_LLM_API_KEY=secret\n"
        "DATABASE_URL=sqlite:///app.db\n",
        encoding="utf-8",
    )

    environment = build_mock_llm_environment(tmp_path, port=9010)

    assert environment["OPENAI_BASE_URL"] == "http://127.0.0.1:9010/v1"
    assert environment["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9010"
    assert environment["CUSTOM_LLM_BASE_URL"] == "http://127.0.0.1:9010/v1"
    assert environment["CUSTOM_LLM_API_KEY"] == "mock-audit-key"
    assert "DATABASE_URL" not in environment


def test_env_detector_uses_explicit_llm_adapters(tmp_path):
    environment = build_mock_llm_environment(
        tmp_path,
        port=9010,
        env_files=[".custom-env"],
        llm_adapters=[
            {
                "name": "embedding",
                "type": "embedding",
                "protocol": "openai",
                "base_url_env": "MODEL_BASE_URL",
                "api_key_env": "MODEL_API_KEY",
                "model_env": "EMBEDDING_MODEL",
            }
        ],
    )

    assert environment["MODEL_BASE_URL"] == "http://127.0.0.1:9010/v1"
    assert environment["MODEL_API_KEY"] == "mock-audit-key"
    assert environment["EMBEDDING_MODEL"] == "mock-embedding-model"


def test_project_rules_override_profile_and_use_priority():
    engine = MockResponseEngine(
        LlmMockConfig.model_validate(
            {
                "profile": "generic-agent",
                "rules": [
                    {
                        "id": "low",
                        "priority": 10,
                        "match": {"contains_any": ["prd"]},
                        "response": {"format": "text", "content": "low"},
                    },
                    {
                        "id": "high",
                        "priority": 20,
                        "match": {"contains_any": ["prd"]},
                        "response": {"format": "json", "content": {"source": "high"}},
                    },
                ],
            }
        )
    )

    rule, content = engine.generate(
        {"messages": [{"role": "user", "content": "Write a PRD"}]}
    )

    assert rule == "project:high"
    assert json.loads(content) == {"source": "high"}


def test_stream_constraint_and_explicit_fallback():
    engine = MockResponseEngine(
        LlmMockConfig.model_validate(
            {
                "profile": None,
                "rules": [
                    {
                        "id": "stream_only",
                        "match": {
                            "contains_any": ["hello"],
                            "stream": True,
                        },
                        "response": {"format": "text", "content": "stream"},
                    }
                ],
                "fallback": {"format": "text", "content": "fallback"},
            }
        )
    )

    rule, content = engine.generate(
        {
            "stream": False,
            "messages": [{"role": "user", "content": "hello"}],
        }
    )

    assert rule == "fallback"
    assert content == "fallback"


def test_no_match_without_profile_or_fallback_is_an_error():
    engine = MockResponseEngine(
        LlmMockConfig.model_construct(
            version=1,
            profile=None,
            rules=[],
            fallback=None,
        )
    )

    with pytest.raises(LookupError, match="no mock LLM rule matched"):
        engine.generate({"messages": [{"role": "user", "content": "unknown"}]})


def test_openai_and_anthropic_compatibility():
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockLlmHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        openai_response = httpx.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": "mock",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Review PRD and return JSON with passed and issues"
                        ),
                    }
                ],
            },
        )
        review = json.loads(
            openai_response.json()["choices"][0]["message"]["content"]
        )
        assert review["passed"] is True

        openai_stream = httpx.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": "mock",
                "stream": True,
                "messages": [{"role": "user", "content": "Write a PRD"}],
            },
        )
        assert "data: [DONE]" in openai_stream.text
        assert "Product Requirements Document" in openai_stream.text

        anthropic_response = httpx.post(
            f"{base_url}/v1/messages",
            json={
                "model": "mock",
                "messages": [{"role": "user", "content": "Generate API docs"}],
            },
        )
        assert anthropic_response.json()["content"][0]["type"] == "text"

        anthropic_stream = httpx.post(
            f"{base_url}/v1/messages",
            json={
                "model": "mock",
                "stream": True,
                "messages": [{"role": "user", "content": "Write a PRD"}],
            },
        )
        assert "event: content_block_delta" in anthropic_stream.text
        assert "event: message_stop" in anthropic_stream.text
        assert '"input_tokens": 0' in anthropic_stream.text

        embeddings_response = httpx.post(
            f"{base_url}/v1/embeddings",
            json={
                "model": "mock-embedding",
                "input": ["hello", "world"],
                "dimensions": 4,
            },
        )
        embeddings = embeddings_response.json()
        assert embeddings["object"] == "list"
        assert len(embeddings["data"]) == 2
        assert len(embeddings["data"][0]["embedding"]) == 4
    finally:
        server.shutdown()
        server.server_close()
        with contextlib.suppress(RuntimeError):
            thread.join(timeout=1)

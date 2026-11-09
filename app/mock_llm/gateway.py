"""Offline LLM gateway used inside sandboxed audits.

The gateway implements a small subset of OpenAI-compatible and Anthropic-style
APIs so projects can run without real LLM network access.
"""

import argparse
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.mock_llm.responses import MockResponseEngine
from app.utils.process import sha256_json

logger = logging.getLogger("audit_harness.mock_llm")
DEFAULT_ENGINE = MockResponseEngine()
TRACE_LOCK = threading.Lock()


class MockLlmHandler(BaseHTTPRequestHandler):
    """HTTP handler for chat completion, message, and embedding mock endpoints."""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        """Serve a small health endpoint for readiness checks."""
        if self.path.rstrip("/") in {"", "/health"}:
            self._write_json(200, {"status": "ok", "service": "mock-llm"})
            return
        self._write_json(404, {"error": {"message": "route not found"}})

    def do_POST(self) -> None:
        """Route mock LLM requests by endpoint shape."""
        try:
            payload = self._read_json()
        except (json.JSONDecodeError, ValueError):
            self._write_json(400, {"error": {"message": "invalid JSON body"}})
            return

        if self.path.rstrip("/").endswith("/embeddings"):
            # Embeddings are deterministic vectors, not rule-based text, because
            # vector DB projects usually only need stable dimensional payloads.
            logger.info(
                "MOCK_EMBEDDING_REQUEST | path=%s | model=%s",
                self.path,
                payload.get("model", "mock-embedding-model"),
            )
            self._write_trace(payload, "embedding")
            self._handle_openai_embeddings(payload)
            return

        engine = getattr(self.server, "response_engine", DEFAULT_ENGINE)
        try:
            rule, content = engine.generate(payload)
        except LookupError as exc:
            logger.warning(
                "MOCK_LLM_NO_MATCH | path=%s | error=%s",
                self.path,
                exc,
            )
            self._write_json(422, {"error": {"message": str(exc)}})
            return
        logger.info(
            "MOCK_LLM_REQUEST | path=%s | model=%s | stream=%s | rule=%s",
            self.path,
            payload.get("model", "mock-model"),
            bool(payload.get("stream")),
            rule,
        )
        self._write_trace(payload, rule)
        # Support both common chat API protocols so projects can switch providers
        # through env vars without changing test harness code.
        if self.path.rstrip("/").endswith("/chat/completions"):
            self._handle_openai(payload, content)
        elif self.path.rstrip("/").endswith("/messages"):
            self._handle_anthropic(payload, content)
        else:
            self._write_json(404, {"error": {"message": "route not found"}})

    def log_message(self, format: str, *args: Any) -> None:
        """Route BaseHTTPRequestHandler access logs into project logging."""
        logger.debug(format, *args)

    def _handle_openai(self, payload: dict[str, Any], content: str) -> None:
        """Write an OpenAI chat completions compatible response."""
        request_id = f"chatcmpl-mock-{uuid4().hex[:12]}"
        model = str(payload.get("model") or "mock-model")
        if payload.get("stream"):
            # Streaming responses use the same SSE shape many SDKs expect from
            # OpenAI, ending with the [DONE] sentinel.
            chunks = [
                {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": content},
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {"index": 0, "delta": {}, "finish_reason": "stop"}
                    ],
                },
            ]
            self._write_sse(
                [f"data: {json.dumps(item, ensure_ascii=False)}\n\n" for item in chunks]
                + ["data: [DONE]\n\n"]
            )
            return
        self._write_json(
            200,
            {
                "id": request_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    def _handle_openai_embeddings(self, payload: dict[str, Any]) -> None:
        """Write an OpenAI embeddings compatible deterministic response."""
        raw_input = payload.get("input", [])
        inputs = raw_input if isinstance(raw_input, list) else [raw_input]
        dimensions = int(payload.get("dimensions") or 1536)
        model = str(payload.get("model") or "mock-embedding-model")
        data = [
            {
                "object": "embedding",
                "index": index,
                "embedding": self._embedding_vector(str(value), dimensions),
            }
            for index, value in enumerate(inputs)
        ]
        self._write_json(
            200,
            {
                "object": "list",
                "data": data,
                "model": model,
                "usage": {
                    "prompt_tokens": len(inputs),
                    "total_tokens": len(inputs),
                },
            },
        )

    @staticmethod
    def _embedding_vector(value: str, dimensions: int) -> list[float]:
        """Generate stable pseudo-embedding values from input text."""
        seed = sum(value.encode("utf-8")) or 1
        return [((seed + index) % 1000) / 1000 for index in range(dimensions)]

    def _handle_anthropic(self, payload: dict[str, Any], content: str) -> None:
        """Write an Anthropic messages compatible response."""
        request_id = f"msg_mock_{uuid4().hex[:12]}"
        model = str(payload.get("model") or "mock-model")
        if payload.get("stream"):
            # Anthropic streaming has named SSE events instead of OpenAI-style
            # anonymous data chunks.
            events = [
                (
                    "message_start",
                    {
                        "type": "message_start",
                        "message": {
                            "id": request_id,
                            "type": "message",
                            "role": "assistant",
                            "model": model,
                            "content": [],
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {"input_tokens": 1, "output_tokens": 0},
                        },
                    },
                ),
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                ),
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": content},
                    },
                ),
                (
                    "content_block_stop",
                    {"type": "content_block_stop", "index": 0},
                ),
                (
                    "message_delta",
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                        "usage": {"input_tokens": 0, "output_tokens": 1},
                    },
                ),
                ("message_stop", {"type": "message_stop"}),
            ]
            self._write_sse(
                [
                    f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                    for event, data in events
                ]
            )
            return
        self._write_json(
            200,
            {
                "id": request_id,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [{"type": "text", "text": content}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    def _read_json(self) -> dict[str, Any]:
        """Read and validate a JSON object request body."""
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            raise ValueError("empty body")
        value = json.loads(self.rfile.read(content_length))
        if not isinstance(value, dict):
            raise ValueError("body must be an object")
        return value

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        """Write a JSON response with explicit Content-Length."""
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_sse(self, events: list[str]) -> None:
        """Write a complete SSE response body."""
        body = "".join(events).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_trace(self, payload: dict[str, Any], rule: str) -> None:
        """Append a privacy-preserving LLM call trace for observability."""
        trace_path = getattr(self.server, "trace_path", None)
        if trace_path is None:
            return
        trace = {
            "timestamp": time.time(),
            "path": self.path,
            "model": payload.get("model", "mock-model"),
            "stream": bool(payload.get("stream")),
            "rule": rule,
            "prompt_hash": sha256_json(
                {
                    "system": payload.get("system"),
                    "messages": payload.get("messages", []),
                }
            ),
        }
        with TRACE_LOCK, trace_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(trace, ensure_ascii=False))
            stream.write("\n")


def main() -> None:
    """CLI entrypoint used by DockerSandboxRunner."""
    parser = argparse.ArgumentParser(description="Offline mock LLM gateway")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9001)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--trace", type=Path)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    server = ThreadingHTTPServer((args.host, args.port), MockLlmHandler)
    # Attach runtime configuration to the HTTP server instance so each handler
    # can stay stateless and thread-safe.
    server.response_engine = (
        MockResponseEngine.from_path(args.config)
        if args.config
        else DEFAULT_ENGINE
    )
    server.trace_path = args.trace
    logger.info(
        "MOCK_LLM_READY | host=%s | port=%s | config=%s | trace=%s",
        args.host,
        args.port,
        args.config or "builtin",
        args.trace or "disabled",
    )
    server.serve_forever()


if __name__ == "__main__":
    main()

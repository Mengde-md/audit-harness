import logging
import time
from typing import Any

from app.core.config import Settings
from app.core.logging import (
    new_request_id,
    parse_body_for_log,
    reset_request_id,
    set_request_id,
)


class RequestLoggingMiddleware:
    def __init__(self, app, settings: Settings):
        self.app = app
        self.settings = settings
        self.logger = logging.getLogger("audit_harness.http")

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        candidate_id = headers.get("x-request-id", "")
        request_id = candidate_id if candidate_id and len(candidate_id) <= 128 and all(32 <= ord(c) < 127 for c in candidate_id) else new_request_id()
        token = set_request_id(request_id)
        started = time.perf_counter()
        request_chunks: list[bytes] = []
        response_chunks: list[bytes] = []
        status_code = 500

        async def receive_wrapper() -> dict[str, Any]:
            message = await receive()
            if message["type"] == "http.request" and self.settings.log_request_body:
                request_chunks.append(message.get("body", b""))
            return message

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = list(message.get("headers", []))
                response_headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = response_headers
            elif (
                message["type"] == "http.response.body"
                and self.settings.log_response_body
            ):
                response_chunks.append(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        finally:
            cost_ms = (time.perf_counter() - started) * 1000
            method = scope.get("method", "")
            path = scope.get("path", "")
            query = scope.get("query_string", b"").decode("utf-8", errors="replace")
            url = f"{path}?{query}" if query else path
            request_body = parse_body_for_log(
                b"".join(request_chunks),
                self.settings.log_body_max_chars,
            )
            response_body = parse_body_for_log(
                b"".join(response_chunks),
                self.settings.log_body_max_chars,
            )
            self.logger.info(
                "API_REQUEST | method=%s | url=%s | body=%s",
                method,
                url,
                request_body,
            )
            self.logger.log(
                logging.ERROR if status_code >= 500 else logging.INFO,
                "API_RESPONSE | method=%s | url=%s | status=%s "
                "| cost=%.2fms | body=%s",
                method,
                url,
                status_code,
                cost_ms,
                response_body,
            )
            reset_request_id(token)

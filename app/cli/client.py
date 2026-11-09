"""HTTP client used by the Audit Harness CLI."""

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class CliError(Exception):
    """Base exception for user-facing CLI failures."""

    exit_code = 1


class ApiError(CliError):
    """Raised when Harness returns a non-zero API response code."""

    exit_code = 1

    def __init__(self, code: int | str, msg: str) -> None:
        """Store API error code and message."""
        self.code = code
        self.msg = msg
        super().__init__(f"code: {code}\nmsg: {msg}")


class HttpRequestError(CliError):
    """Raised when the CLI cannot reach the Harness HTTP service."""

    exit_code = 3


class HarnessHttpClient:
    """Small standard-library HTTP client for Harness API calls."""

    def __init__(self, server: str, timeout: int = 30) -> None:
        """Create a client for one Harness server."""
        self.server = server.rstrip("/")
        self.timeout = timeout

    def create_audit(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create an audit task."""
        return self._request("POST", "/api/v1/audits", payload)

    def get_status(self, audit_id: str) -> dict[str, Any]:
        """Fetch task status."""
        return self._request("GET", f"/api/v1/audits/{quote(audit_id)}")

    def get_report(self, audit_id: str) -> dict[str, Any]:
        """Fetch structured audit report."""
        return self._request("GET", f"/api/v1/audits/{quote(audit_id)}/report")

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send one JSON request and unwrap the standard API response."""
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.server}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise HttpRequestError(f"HTTP {exc.code}: {raw}") from exc
        except URLError as exc:
            raise HttpRequestError(str(exc.reason)) from exc
        except OSError as exc:
            raise HttpRequestError(str(exc)) from exc
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HttpRequestError(f"invalid JSON response: {raw[:500]}") from exc
        if envelope.get("code", 0) != 0:
            raise ApiError(envelope.get("code"), envelope.get("msg", "request failed"))
        return envelope.get("data") or {}

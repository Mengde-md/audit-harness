import json
import re
import time
from urllib.parse import urlsplit

import httpx

from app.core.config import Settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import LlmError
from app.core.logging import log_llm_error, log_llm_request, log_llm_response
from app.providers.llm.base import LlmProvider


class OpenAICompatibleLlmProvider(LlmProvider):
    def __init__(self, settings: Settings):
        self.settings = settings

    async def generate_json(self, *, system_prompt: str, user_prompt: str) -> dict:
        if not self.settings.llm_api_key or not self.settings.llm_model:
            raise LlmError(
                "LLM_API_KEY and LLM_MODEL must be configured",
                error=ErrorCode.LLM_CONFIG_ERROR,
            )

        url = f"{self.settings.llm_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.llm_model,
            "temperature": self.settings.llm_temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if urlsplit(self.settings.llm_base_url).hostname == "api.deepseek.com":
            # The harness needs the final JSON contract, not a reasoning-only
            # response. Keep provider-specific fields off generic OpenAI APIs.
            payload["thinking"] = {"type": "disabled"}
            payload["max_tokens"] = 8192
        prompt_for_log = (
            {"system": system_prompt, "user": user_prompt}
            if self.settings.log_llm_content
            else "<content logging disabled>"
        )
        log_llm_request(
            model=self.settings.llm_model,
            prompt=prompt_for_log,
            max_chars=self.settings.log_body_max_chars,
        )
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.llm_timeout_seconds
            ) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            cost_ms = (time.perf_counter() - started) * 1000
            log_llm_error(model=self.settings.llm_model, cost_ms=cost_ms, error=exc)
            raise LlmError(str(exc), error=ErrorCode.LLM_TIMEOUT) from exc
        except httpx.HTTPStatusError as exc:
            cost_ms = (time.perf_counter() - started) * 1000
            log_llm_error(model=self.settings.llm_model, cost_ms=cost_ms, error=exc)
            status = exc.response.status_code
            error = {
                401: ErrorCode.LLM_AUTH_ERROR,
                403: ErrorCode.LLM_AUTH_ERROR,
                404: ErrorCode.LLM_MODEL_ERROR,
                429: ErrorCode.LLM_RATE_LIMIT,
            }.get(status, ErrorCode.LLM_ERROR)
            raise LlmError(
                f"LLM HTTP status {status}",
                error=error,
                context={"status_code": status, "model": self.settings.llm_model},
            ) from exc
        except httpx.HTTPError as exc:
            cost_ms = (time.perf_counter() - started) * 1000
            log_llm_error(model=self.settings.llm_model, cost_ms=cost_ms, error=exc)
            raise LlmError(str(exc), error=ErrorCode.LLM_ERROR) from exc
        try:
            choice = response.json()["choices"][0]
            content = choice["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            cost_ms = (time.perf_counter() - started) * 1000
            log_llm_error(model=self.settings.llm_model, cost_ms=cost_ms, error=exc)
            raise LlmError(
                f"LLM response payload missing content: {exc}",
                error=ErrorCode.LLM_NO_RESPONSE,
            ) from exc

        if not isinstance(content, str) or not content.strip():
            raise LlmError(
                "LLM response content is empty or is not text",
                error=ErrorCode.LLM_NO_RESPONSE,
                context={"model": self.settings.llm_model},
            )
        if choice.get("finish_reason") == "length":
            raise LlmError(
                "LLM response was truncated by the output-token limit",
                error=ErrorCode.LLM_INVALID_RESPONSE,
                context={"model": self.settings.llm_model, "finish_reason": "length"},
            )

        cost_ms = (time.perf_counter() - started) * 1000
        log_llm_response(
            model=self.settings.llm_model,
            response=content if self.settings.log_llm_content else "<content logging disabled>",
            cost_ms=cost_ms,
            max_chars=self.settings.log_body_max_chars,
        )

        try:
            return self._loads_json_object(content)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            log_llm_error(model=self.settings.llm_model, cost_ms=cost_ms, error=exc)
            context = {
                "model": self.settings.llm_model,
                "parse_error": str(exc),
            }
            if self.settings.log_llm_content:
                context["raw_response_excerpt"] = content[:2000]
            raise LlmError(
                "LLM returned invalid JSON",
                error=ErrorCode.LLM_INVALID_RESPONSE,
                context=context,
            ) from exc

    @staticmethod
    def _strip_code_fence(content: str) -> str:
        match = re.fullmatch(r"\s*```(?:json)?\s*(.*?)\s*```\s*", content, re.DOTALL)
        return match.group(1) if match else content

    @classmethod
    def _loads_json_object(cls, content: str) -> dict:
        """Load a JSON object, tolerating prose around the first JSON block."""
        stripped = cls._strip_code_fence(content)
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = json.loads(cls._extract_first_json(stripped))
        if not isinstance(payload, dict):
            raise ValueError("LLM JSON response must be an object")
        return payload

    @staticmethod
    def _extract_first_json(content: str) -> str:
        """Extract the first complete JSON object/array from mixed text."""
        start = -1
        opening = ""
        for index, char in enumerate(content):
            if char in "{[":
                start = index
                opening = char
                break
        if start < 0:
            raise json.JSONDecodeError("No JSON object found", content, 0)

        closing = "}" if opening == "{" else "]"
        stack = [closing]
        in_string = False
        escaped = False
        for index in range(start + 1, len(content)):
            char = content[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                continue
            if char in "{[":
                stack.append("}" if char == "{" else "]")
                continue
            if char in "}]":
                if not stack or char != stack[-1]:
                    raise json.JSONDecodeError("Mismatched JSON brackets", content, index)
                stack.pop()
                if not stack:
                    return content[start : index + 1]
        raise json.JSONDecodeError("Unterminated JSON object", content, start)

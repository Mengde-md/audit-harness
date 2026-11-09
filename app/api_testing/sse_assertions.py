import json


class SseAssertionEngine:
    def assert_sse(self, response_text: str) -> tuple[list[dict], list[str]]:
        events: list[dict] = []
        failures: list[str] = []
        terminal_seen = False

        for line in response_text.splitlines():
            if not line.startswith("data:"):
                continue
            raw_data = line[5:].strip()
            if not raw_data:
                continue
            if raw_data == "[DONE]":
                terminal_seen = True
                events.append({"type": "done_marker"})
                continue
            try:
                payload = json.loads(raw_data)
            except json.JSONDecodeError:
                events.append({"type": "text", "data": raw_data[:1000]})
                continue

            if isinstance(payload, dict):
                event = payload
            else:
                event = {"type": "data", "data": payload}
            events.append(event)
            event_type = str(event.get("type", "")).lower()
            if event_type == "error":
                failures.append(
                    "SSE error event: "
                    f"{event.get('message') or event.get('content') or event}"
                )
            elif event_type == "done":
                terminal_seen = True

        if not events:
            failures.append("response contains no SSE data events")
        if not terminal_seen:
            failures.append("SSE stream did not contain a done event or [DONE] marker")
        return events, failures

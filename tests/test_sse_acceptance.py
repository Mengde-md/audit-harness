from app.api_testing.http_acceptance_runner import HttpAcceptanceRunner


def test_sse_error_event_fails():
    events, failures = HttpAcceptanceRunner()._assert_sse(
        'data: {"type":"error","message":"Connection error."}\n\n'
        "data: [DONE]\n\n"
    )

    assert events[-1]["type"] == "done_marker"
    assert failures == ["SSE error event: Connection error."]


def test_sse_chunks_and_done_pass():
    events, failures = HttpAcceptanceRunner()._assert_sse(
        'data: {"type":"chunk","content":"hello"}\n\n'
        'data: {"type":"done"}\n\n'
        "data: [DONE]\n\n"
    )

    assert len(events) == 3
    assert failures == []


def test_sse_requires_terminal_event():
    _, failures = HttpAcceptanceRunner()._assert_sse(
        'data: {"type":"chunk","content":"hello"}\n\n'
    )

    assert failures == ["SSE stream did not contain a done event or [DONE] marker"]


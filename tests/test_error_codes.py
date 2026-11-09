from app.core.error_codes import ErrorCode
from app.core.exceptions import InvalidInputError, raise_error


def test_error_code_ranges():
    assert ErrorCode.SUCCESS.code == 0
    assert 20000 <= ErrorCode.PARAM_ERROR.code <= 29999
    assert 30000 <= ErrorCode.DB_ERROR.code <= 39999
    assert 40000 <= ErrorCode.LLM_ERROR.code <= 49999
    assert 50000 <= ErrorCode.SERVER_ERROR.code <= 59999


def test_exception_keeps_public_and_internal_messages_separate():
    error = InvalidInputError(
        "raw parser failure at line 12",
        context={"audit_id": "audit-1"},
    )

    assert error.code == 20001
    assert error.public_message == "请求参数错误"
    assert str(error) == "raw parser failure at line 12"
    assert error.context == {"audit_id": "audit-1"}


def test_raise_error_helper():
    try:
        raise_error(ErrorCode.LLM_TIMEOUT, detail="upstream timed out")
    except Exception as exc:
        assert exc.code == 40001
        assert exc.public_message == "大模型响应超时"
    else:
        raise AssertionError("raise_error did not raise")


import pytest

from app.utils.process import truncate_tail


@pytest.mark.parametrize("limit, expected", [(0, ""), (-1, ""), (4, "tail"), (20, "log tail")])
def test_truncate_tail_honors_log_limit(limit, expected):
    assert truncate_tail("log tail", limit) == expected

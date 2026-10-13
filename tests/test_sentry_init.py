import pytest

from oxidized_mcp.sentry_init import init_sentry


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_dsn_leaves_sentry_off(monkeypatch, value):
    monkeypatch.setenv("SENTRY_DSN", value)

    assert init_sentry() is False

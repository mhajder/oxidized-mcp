import sys
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from oxidized_mcp.sentry_init import init_sentry


@pytest.fixture
def fake_sentry(monkeypatch):
    """A stand-in sentry_sdk, so the test records init() without sending anything."""
    sdk = MagicMock()
    monkeypatch.setenv("SENTRY_DSN", "https://key@example.invalid/1")
    monkeypatch.delenv("SENTRY_SEND_DEFAULT_PII", raising=False)
    with patch.dict(
        sys.modules,
        {
            "sentry_sdk": sdk,
            "sentry_sdk.integrations": MagicMock(),
            "sentry_sdk.integrations.mcp": MagicMock(),
        },
    ):
        yield sdk.init


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_dsn_leaves_sentry_off(monkeypatch, value):
    monkeypatch.setenv("SENTRY_DSN", value)

    assert init_sentry() is False


def test_sentry_sends_no_pii_unless_asked(fake_sentry):
    """Tool results are full device configurations, secrets included."""
    assert init_sentry() is True
    assert fake_sentry.call_args.kwargs["send_default_pii"] is False


def test_sentry_pii_can_still_be_opted_into(fake_sentry, monkeypatch):
    monkeypatch.setenv("SENTRY_SEND_DEFAULT_PII", "true")
    init_sentry()
    assert fake_sentry.call_args.kwargs["send_default_pii"] is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "trace"),
        ("manual", "manual"),
        ("MANUAL", "manual"),
        (" Manual ", "manual"),
        ("trace", "trace"),
    ],
)
def test_profile_lifecycle_ignores_case(fake_sentry, monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("SENTRY_PROFILE_LIFECYCLE", raising=False)
    else:
        monkeypatch.setenv("SENTRY_PROFILE_LIFECYCLE", value)
    init_sentry()
    assert fake_sentry.call_args.kwargs["profile_lifecycle"] == expected


def test_unknown_profile_lifecycle_warns_and_uses_trace(
    fake_sentry, monkeypatch, caplog
):
    monkeypatch.setenv("SENTRY_PROFILE_LIFECYCLE", "Continuous")
    init_sentry()
    assert fake_sentry.call_args.kwargs["profile_lifecycle"] == "trace"
    # Quoted as set, so it can be found in the environment.
    assert "Unknown SENTRY_PROFILE_LIFECYCLE 'Continuous'" in caplog.text


@pytest.mark.usefixtures("fake_sentry")
def test_known_profile_lifecycle_does_not_warn(monkeypatch, caplog):
    monkeypatch.setenv("SENTRY_PROFILE_LIFECYCLE", "manual")
    init_sentry()
    assert "SENTRY_PROFILE_LIFECYCLE" not in caplog.text

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


def test_missing_sentry_sdk_warns_and_stays_off(monkeypatch, caplog):
    monkeypatch.setenv("SENTRY_DSN", "https://key@example.invalid/1")
    # A None entry makes the import raise ImportError.
    with patch.dict(sys.modules, {"sentry_sdk": None}):
        assert init_sentry() is False
    assert "Sentry SDK not installed" in caplog.text


def test_explicit_release_is_used(fake_sentry, monkeypatch):
    monkeypatch.setenv("SENTRY_RELEASE", "1.2.3")
    init_sentry()
    assert fake_sentry.call_args.kwargs["release"] == "1.2.3"


def test_release_is_none_when_the_package_is_not_installed(fake_sentry, monkeypatch):
    import importlib.metadata

    def missing(_name):
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.delenv("SENTRY_RELEASE", raising=False)
    monkeypatch.setattr(importlib.metadata, "version", missing)
    init_sentry()
    assert fake_sentry.call_args.kwargs["release"] is None


def test_init_failure_is_logged_not_raised(fake_sentry, caplog):
    fake_sentry.side_effect = RuntimeError("bad dsn")
    assert init_sentry() is False
    assert "Failed to initialize Sentry: bad dsn" in caplog.text

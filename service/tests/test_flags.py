import pytest

from traintracker.flags import client as flags_client


@pytest.fixture(autouse=True)
def _reset_flags_state(monkeypatch):
    monkeypatch.setattr(flags_client, "_client", None)
    monkeypatch.setattr(flags_client, "_cached_flags", None)
    monkeypatch.setattr(flags_client, "_cached_at", 0.0)
    monkeypatch.delenv("FLAGSMITH_SERVER_ENV_KEY", raising=False)


def test_defaults_off_without_env_key():
    assert flags_client.is_enabled("some_flag") is False
    assert flags_client.is_enabled("some_flag", default=True) is True


def test_defaults_when_fetch_fails(monkeypatch):
    monkeypatch.setenv("FLAGSMITH_SERVER_ENV_KEY", "fake-key")

    class BrokenClient:
        def get_environment_flags(self):
            raise RuntimeError("network down")

    monkeypatch.setattr(flags_client, "_get_client", lambda: BrokenClient())

    assert flags_client.is_enabled("some_flag", default=False) is False


def test_uses_cached_flags_within_window(monkeypatch):
    monkeypatch.setenv("FLAGSMITH_SERVER_ENV_KEY", "fake-key")

    calls = {"n": 0}

    class FakeFlags:
        def is_feature_enabled(self, name):
            return True

    class FakeClient:
        def get_environment_flags(self):
            calls["n"] += 1
            return FakeFlags()

    monkeypatch.setattr(flags_client, "_get_client", lambda: FakeClient())

    assert flags_client.is_enabled("my_flag") is True
    assert flags_client.is_enabled("my_flag") is True
    assert calls["n"] == 1

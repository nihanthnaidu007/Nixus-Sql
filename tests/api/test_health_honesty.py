"""Honest /api/health (7.2 amendment, defect C).

A placeholder/empty LLM key must report that provider as NOT connected WITHOUT
making any provider API call, and the health handler must still succeed (the
endpoint stays HTTP 200 — a failed dependency changes only a body field, never
the status, so a compose healthcheck can't restart a live container).

Provider probes are mocked; no real network calls are made.
"""
import asyncio

import pytest

from api import main


@pytest.fixture(autouse=True)
def _reset_health_cache():
    """Clear the module-level LLM connectivity cache before each test so the
    probe logic actually runs (it is cached for LLM_HEALTH_CACHE_TTL seconds)."""
    main._llm_health_cache = {"status": "unknown", "checked_at": 0.0}
    yield


def test_placeholder_keys_report_not_connected_without_api_call(monkeypatch):
    monkeypatch.setattr(main.settings, "anthropic_api_key", "your_anthropic_api_key_here")
    monkeypatch.setattr(main.settings, "openai_api_key", "your_openai_api_key_here")

    # Any attempt to construct a provider client must fail the test: a placeholder
    # key must short-circuit BEFORE the import/instantiation runs.
    import anthropic
    def _boom(*a, **k):
        raise AssertionError("provider client must NOT be constructed for a placeholder key")
    monkeypatch.setattr(anthropic, "Anthropic", _boom)

    result = asyncio.run(main._check_llm_connectivity())
    assert result["anthropic_connected"] is False
    assert result["openai_connected"] is False
    assert result["status"] == "degraded"


def test_real_key_uses_probe_and_reports_actual_result(monkeypatch):
    monkeypatch.setattr(main.settings, "anthropic_api_key", "sk-ant-real-1234567890")
    monkeypatch.setattr(main.settings, "openai_api_key", "sk-proj-real-1234567890")

    import anthropic
    class _FakeModels:
        def list(self, *a, **k):
            return ["model"]
    class _FakeAnthropic:
        def __init__(self, *a, **k):
            self.models = _FakeModels()
    monkeypatch.setattr(anthropic, "Anthropic", _FakeAnthropic)

    import openai
    class _FakeAsyncModels:
        async def list(self, *a, **k):
            return ["model"]
    class _FakeAsyncOpenAI:
        def __init__(self, *a, **k):
            self.models = _FakeAsyncModels()
    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeAsyncOpenAI)

    result = asyncio.run(main._check_llm_connectivity())
    assert result["anthropic_connected"] is True
    assert result["openai_connected"] is True
    assert result["status"] == "ok"


def test_health_endpoint_stays_200_on_bad_keys(monkeypatch):
    """The handler returns a normal dict (HTTP 200) even when LLM keys are bad;
    only the body fields reflect the degraded dependency."""
    monkeypatch.setattr(main.settings, "anthropic_api_key", "your_anthropic_api_key_here")
    monkeypatch.setattr(main.settings, "openai_api_key", "your_openai_api_key_here")

    # Avoid a real DB dependency; DB health is orthogonal to this test.
    async def _fake_db():
        return True
    monkeypatch.setattr(main, "check_db_connection", _fake_db)
    # langsmith_tracing reflects the process-global tracing state (captured at
    # import); pin it deterministically here — the gate itself is covered by
    # tests/utils/test_credentials_and_tracing.py.
    monkeypatch.setattr(main, "is_tracing_enabled", lambda: False)

    body = asyncio.run(main.health())
    assert isinstance(body, dict)               # a normal return ⇒ FastAPI 200
    assert body["anthropic_connected"] is False
    assert body["openai_connected"] is False
    assert body["status"] == "degraded"         # body reflects the bad keys
    assert body["langsmith_tracing"] is False   # field wired from is_tracing_enabled()

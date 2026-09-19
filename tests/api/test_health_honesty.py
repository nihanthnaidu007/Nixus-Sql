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


def test_ollama_provider_reports_ollama_connected_and_never_probes_openai(
    monkeypatch,
):
    """Under EMBEDDINGS_PROVIDER=ollama the health payload must key embeddings
    off Ollama reachability — OpenAI is never constructed, never probed."""
    monkeypatch.setattr(main.settings, "embeddings_provider", "ollama")
    monkeypatch.setattr(main.settings, "anthropic_api_key", "your_anthropic_api_key_here")

    import openai

    def _boom(*a, **k):
        raise AssertionError("OpenAI client must NOT be probed under ollama")

    monkeypatch.setattr(openai, "AsyncOpenAI", _boom)

    async def _ollama_up():
        return True

    monkeypatch.setattr(
        "nixus.utils.embeddings.check_ollama_reachable", _ollama_up
    )

    result = asyncio.run(main._check_llm_connectivity())
    assert result["embeddings_provider"] == "ollama"
    assert result["ollama_connected"] is True
    assert result["openai_connected"] is False  # not probed — inactive provider
    # anthropic key is the placeholder sentinel → the honest status is degraded.
    assert result["anthropic_connected"] is False
    assert result["status"] == "degraded"


def test_ollama_provider_reports_degraded_when_ollama_unreachable(monkeypatch):
    monkeypatch.setattr(main.settings, "embeddings_provider", "ollama")
    monkeypatch.setattr(main.settings, "anthropic_api_key", "your_anthropic_api_key_here")

    async def _ollama_down():
        return False

    monkeypatch.setattr(
        "nixus.utils.embeddings.check_ollama_reachable", _ollama_down
    )

    result = asyncio.run(main._check_llm_connectivity())
    assert result["ollama_connected"] is False
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


# ── Active-provider payload contract ─────────────────────────────────────────
#
# The health payload must report the ACTIVE embeddings provider and its
# connectivity, and status/degraded semantics must come from the active
# providers only: under EMBEDDINGS_PROVIDER=ollama an unprobed
# openai_connected=false is "not active", never a failure. Provider probes are
# mocked; no real network calls are made.

def _mock_probes_up(monkeypatch):
    """Mock both provider probes as reachable (no network, no quota)."""
    import anthropic
    import openai

    class _FakeModels:
        def list(self, *a, **k):
            return ["model"]

    class _FakeAnthropic:
        def __init__(self, *a, **k):
            self.models = _FakeModels()

    class _FakeAsyncModels:
        async def list(self, *a, **k):
            return ["model"]

    class _FakeAsyncOpenAI:
        def __init__(self, *a, **k):
            self.models = _FakeAsyncModels()

    monkeypatch.setattr(anthropic, "Anthropic", _FakeAnthropic)
    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeAsyncOpenAI)


def _with_db_up(monkeypatch):
    async def _db():
        return True
    monkeypatch.setattr(main, "check_db_connection", _db)
    monkeypatch.setattr(main, "is_tracing_enabled", lambda: False)


def test_health_payload_openai_provider_reports_openai_fields(monkeypatch):
    """Under the default openai provider the payload keeps today's semantics and
    adds the provider fields: openai is the active embeddings provider."""
    monkeypatch.setattr(main.settings, "embeddings_provider", "openai")
    monkeypatch.setattr(main.settings, "anthropic_api_key", "sk-ant-real-1234567890")
    monkeypatch.setattr(main.settings, "openai_api_key", "sk-proj-real-1234567890")
    _mock_probes_up(monkeypatch)
    _with_db_up(monkeypatch)

    body = asyncio.run(main.health())
    assert body["embeddings_provider"] == "openai"
    assert body["openai_connected"] is True
    assert body["ollama_connected"] is False    # not active, not probed
    assert body["embedding_dim"] == 1536        # settings.embedding_dim under openai
    assert body["status"] == "ok"
    assert body["degraded_reasons"] == []


def test_health_ollama_active_openai_false_does_not_degrade(monkeypatch):
    """THE contract: ollama active + ollama up + openai unprobed ⇒ status ok.
    openai_connected=false must not read as an embeddings failure."""
    monkeypatch.setattr(main.settings, "embeddings_provider", "ollama")
    monkeypatch.setattr(main.settings, "anthropic_api_key", "sk-ant-real-1234567890")
    _mock_probes_up(monkeypatch)

    async def _ollama_up():
        return True
    monkeypatch.setattr(
        "nixus.utils.embeddings.check_ollama_reachable", _ollama_up
    )
    _with_db_up(monkeypatch)

    body = asyncio.run(main.health())
    assert body["embeddings_provider"] == "ollama"
    assert body["ollama_connected"] is True
    assert body["openai_connected"] is False    # inactive provider, not probed
    assert body["embedding_dim"] == main.settings.ollama_embedding_dim
    assert body["status"] == "ok"               # NOT degraded by the openai flag
    assert body["degraded_reasons"] == []


def test_health_ollama_down_degrades_with_actionable_fix(monkeypatch):
    """ollama active + ollama down ⇒ degraded, with a reason naming the fix."""
    monkeypatch.setattr(main.settings, "embeddings_provider", "ollama")
    monkeypatch.setattr(main.settings, "anthropic_api_key", "sk-ant-real-1234567890")
    _mock_probes_up(monkeypatch)

    async def _ollama_down():
        return False
    monkeypatch.setattr(
        "nixus.utils.embeddings.check_ollama_reachable", _ollama_down
    )
    _with_db_up(monkeypatch)

    body = asyncio.run(main.health())
    assert body["status"] == "degraded"
    assert body["ollama_connected"] is False
    reasons = " ".join(body["degraded_reasons"])
    assert "ollama" in reasons.lower()
    assert "EMBEDDINGS_PROVIDER" in reasons     # names the knob that fixes it


def test_both_health_routes_serve_the_enriched_payload(monkeypatch):
    """/api/v1/health and its unversioned /api/health alias share one handler —
    both must carry the active-provider fields (guards a future response_model
    or alias drift stripping them from one route)."""
    from fastapi.testclient import TestClient

    key = "test-api-key-123"
    monkeypatch.setattr(main.settings, "api_key", key)
    monkeypatch.setattr(main.settings, "embeddings_provider", "ollama")
    _with_db_up(monkeypatch)

    async def _llm():
        return {
            "status": "ok",
            "embeddings_provider": "ollama",
            "anthropic_connected": True,
            "openai_connected": False,
            "ollama_connected": True,
            "degraded_reasons": [],
            "checked_at": 0.0,
        }
    monkeypatch.setattr(main, "_check_llm_connectivity", _llm)

    client = TestClient(main.app)
    routes = [
        ("/api/health", {}),                    # auth-exempt infra probe
        ("/api/v1/health", {"headers": {"X-API-Key": key}}),
    ]
    for path, kwargs in routes:
        resp = client.get(path, **kwargs)
        assert resp.status_code == 200, path
        body = resp.json()
        assert body["embeddings_provider"] == "ollama", path
        assert body["ollama_connected"] is True, path
        assert body["openai_connected"] is False, path
        assert body["embedding_dim"] == main.settings.embedding_dim, path
        assert body["degraded_reasons"] == [], path
        assert body["status"] == "ok", path

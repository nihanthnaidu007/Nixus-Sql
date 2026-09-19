"""Typed provider-failure envelope (provider resilience).

A provider-side failure must NEVER surface as the generic 500 "An unexpected
error occurred." — the API classifies known provider errors (api/errors.py)
and answers with a 503 envelope naming the provider and the actionable fix.
Everything NOT recognized stays on the 500 handler so unknown bugs keep their
full server-side traceback treatment. Mid-stream provider failures surface as
an `error` SSE event carrying the same envelope, never a silent stream death.

All provider errors are raised from a mocked run path — no network, no keys.
"""
import httpx
import openai
import pytest
from anthropic import AuthenticationError as AnthropicAuthError
from fastapi.testclient import TestClient

from api import main

KEY = "test-api-key-123"
HEADERS = {"X-API-Key": KEY}

ENVELOPE_DETAIL = (
    "Embedding provider 'openai' rejected the configured credentials "
    "(authentication failed)"
)


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture
def _auth(monkeypatch):
    monkeypatch.setattr(main.settings, "api_key", KEY)


def _patch_registry(monkeypatch):
    async def _exists(session_id: str) -> bool:
        return True

    async def _register(session_id: str) -> None:
        pass

    from api import sessions

    monkeypatch.setattr(sessions, "session_exists", _exists)
    monkeypatch.setattr(sessions, "register_session", _register)


def _fail_run(monkeypatch, exc: Exception):
    async def _run(user_query, session_id, **kwargs):
        raise exc

    monkeypatch.setattr(main, "run_query", _run)


def _openai_auth_error() -> openai.AuthenticationError:
    request = httpx.Request("POST", "https://api.openai.com/v1/embeddings")
    response = httpx.Response(401, request=request, json={"error": {"code": "invalid_api_key"}})
    return openai.AuthenticationError("Incorrect API key provided.", response=response, body=None)


def _anthropic_auth_error() -> AnthropicAuthError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(401, request=request, json={"error": {"type": "authentication_error"}})
    return AnthropicAuthError("invalid x-api-key", response=response, body=None)


# ── Non-streaming /run ───────────────────────────────────────────────────────
def test_openai_auth_failure_returns_503_envelope(_auth, client, monkeypatch):
    _patch_registry(monkeypatch)
    _fail_run(monkeypatch, _openai_auth_error())
    resp = client.post("/api/v1/run", json={"user_query": "q", "session_id": "s"}, headers=HEADERS)

    assert resp.status_code == 503  # the provider-failure status, never a raw 500
    body = resp.json()
    assert body["error"] == "LLM provider unavailable"
    assert body["provider"] == "openai"
    assert "OPENAI_API_KEY" in body["detail"]
    assert "EMBEDDINGS_PROVIDER=ollama" in body["detail"]
    assert body["trace_id"]


def test_envelope_detail_matches_the_known_contract(_auth, client, monkeypatch):
    _patch_registry(monkeypatch)
    _fail_run(monkeypatch, _openai_auth_error())
    resp = client.post("/api/v1/run", json={"user_query": "q", "session_id": "s"}, headers=HEADERS)

    assert resp.json()["detail"] == ENVELOPE_DETAIL + " — set a valid " \
        "OPENAI_API_KEY, or set EMBEDDINGS_PROVIDER=ollama to embed locally with Ollama."


def test_anthropic_auth_failure_names_anthropic(_auth, client, monkeypatch):
    _patch_registry(monkeypatch)
    _fail_run(monkeypatch, _anthropic_auth_error())
    resp = client.post("/api/v1/run", json={"user_query": "q", "session_id": "s"}, headers=HEADERS)

    assert resp.status_code == 503
    body = resp.json()
    assert body["provider"] == "anthropic"
    assert "ANTHROPIC_API_KEY" in body["detail"]


@pytest.mark.parametrize(
    ("exc", "provider"),
    [
        (_openai_auth_error(), "openai"),
        (_anthropic_auth_error(), "anthropic"),
    ],
)
def test_provider_errors_are_never_500(_auth, client, monkeypatch, exc, provider):
    _patch_registry(monkeypatch)
    _fail_run(monkeypatch, exc)
    resp = client.post("/api/v1/run", json={"user_query": "q", "session_id": "s"}, headers=HEADERS)

    assert resp.status_code != 500
    assert resp.status_code == 503
    assert resp.json()["provider"] == provider


def test_unknown_error_still_gets_the_generic_500(_auth, client, monkeypatch):
    _patch_registry(monkeypatch)
    _fail_run(monkeypatch, ValueError("a genuine app bug"))
    resp = client.post("/api/v1/run", json={"user_query": "q", "session_id": "s"}, headers=HEADERS)

    assert resp.status_code == 500
    body = resp.json()
    assert body["error"] == "An unexpected error occurred."
    assert body["type"] == "ValueError"


def test_pgvector_dimension_mismatch_maps_to_503_with_reembed_fix(
    _auth, client, monkeypatch
):
    _patch_registry(monkeypatch)
    _fail_run(monkeypatch, RuntimeError("expected 1536 dimensions, not 768"))
    resp = client.post("/api/v1/run", json={"user_query": "q", "session_id": "s"}, headers=HEADERS)

    assert resp.status_code == 503
    body = resp.json()
    assert body["provider"] == "pgvector"
    assert "reembed-stores" in body["detail"]


def test_local_embedding_provider_error_carries_provider_name(
    _auth, client, monkeypatch
):
    from nixus.utils.embeddings import EmbeddingProviderError

    _patch_registry(monkeypatch)
    _fail_run(monkeypatch, EmbeddingProviderError("ollama", "Ollama is down."))
    resp = client.post("/api/v1/run", json={"user_query": "q", "session_id": "s"}, headers=HEADERS)

    assert resp.status_code == 503
    body = resp.json()
    assert body["provider"] == "ollama"
    assert "Ollama is down." in body["detail"]


# ── Streaming /stream ────────────────────────────────────────────────────────
class _ExplodingGraph:
    """A graph whose astream_events raises the given exception immediately."""

    def __init__(self, exc: Exception):
        self._exc = exc

    def astream_events(self, *args, **kwargs):
        raise self._exc


def test_stream_provider_failure_emits_envelope_error_event(
    _auth, client, monkeypatch
):
    _patch_registry(monkeypatch)
    monkeypatch.setattr(main, "build_graph", lambda: _ExplodingGraph(_openai_auth_error()))

    resp = client.post(
        "/api/v1/stream",
        json={"user_query": "q", "session_id": "s"},
        headers=HEADERS,
    )
    text = resp.text

    assert "event: error" in text
    assert "LLM provider unavailable" in text
    assert "OPENAI_API_KEY" in text  # the actionable detail reaches the stream


def test_stream_non_provider_failure_keeps_bare_error_event(
    _auth, client, monkeypatch
):
    _patch_registry(monkeypatch)
    monkeypatch.setattr(main, "build_graph", lambda: _ExplodingGraph(ValueError("boom")))
    resp = client.post(
        "/api/v1/stream",
        json={"user_query": "q", "session_id": "s"},
        headers=HEADERS,
    )
    assert "boom" in resp.text
    assert "LLM provider unavailable" not in resp.text

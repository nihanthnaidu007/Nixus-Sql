"""EMBEDDINGS_PROVIDER dispatch (openai default | ollama) — fully offline.

The openai path must stay behavior-identical (same model, same normalization,
same client seam). The ollama path must complete with ZERO OpenAI usage — no
client construction, no API call — even when no OPENAI_API_KEY exists at all.
Provider-side failures raise the typed EmbeddingProviderError (mapped to the
503 envelope by api/errors.py), never a raw KeyError/TypeError/connection
traceback from inside the retrieval pipeline.

The Ollama HTTP layer is faked at the httpx boundary; the response parser is
tested pure. (The live Ollama /api/embed shape was verified against the real
running Ollama this session — POST {model, input} → {embeddings: [[...]]}.)
"""
import httpx
import pytest
from pydantic import ValidationError

from nixus.config import Settings, settings
from nixus.utils import embeddings


@pytest.fixture(autouse=True)
def _reset_dim_verification(monkeypatch):
    """Each test starts with the one-time ollama dimension check un-verified."""
    monkeypatch.setattr(embeddings, "_ollama_dim_verified", False)


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"embeddings": []}
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeHttpClient:
    """Replaces httpx.AsyncClient — records requests, returns canned responses."""

    last_request: tuple | None = None
    next_response: _FakeResponse | httpx.HTTPError = _FakeResponse()
    raise_connect: httpx.HTTPError | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        type(self).last_request = (url, json)
        if self.raise_connect is not None:
            raise self.raise_connect
        if isinstance(self.next_response, httpx.HTTPError):
            raise self.next_response
        return self.next_response


@pytest.fixture
def fake_http(monkeypatch):
    _FakeHttpClient.last_request = None
    _FakeHttpClient.next_response = _FakeResponse()
    _FakeHttpClient.raise_connect = None
    monkeypatch.setattr(embeddings.httpx, "AsyncClient", _FakeHttpClient)
    return _FakeHttpClient


@pytest.fixture
def ollama_mode(monkeypatch):
    monkeypatch.setattr(settings, "embeddings_provider", "ollama")


@pytest.fixture
def no_openai_allowed(monkeypatch):
    """Any OpenAI usage — client construction or API call — fails the test."""

    def _boom(*a, **k):
        raise AssertionError("OpenAI client must NOT be constructed under ollama")

    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _boom)
    monkeypatch.setattr(embeddings, "_async_client", None)


# ── Default provider: openai, unchanged ──────────────────────────────────────
def test_default_provider_is_openai():
    assert settings.embeddings_provider == "openai"
    assert settings.embedding_dim == 1536  # text-embedding-3-small


def test_openai_path_unchanged_model_and_normalization(monkeypatch):
    captured = {}

    class _FakeEmbeddings:
        async def create(self, input, model):
            captured["input"] = input
            captured["model"] = model

            class _Item:
                embedding = [0.1] * 1536
                index = 0

            class _Data:
                data = [_Item()]

            return _Data()

    class _FakeClient:
        embeddings = _FakeEmbeddings()

    monkeypatch.setattr(embeddings, "_async_client", _FakeClient())
    vector = _run(embeddings.embed_text("what\n is\n revenue?"))
    assert captured["model"] == embeddings.EMBEDDING_MODEL == "text-embedding-3-small"
    assert captured["input"] == ["what  is  revenue?"]  # newlines folded, stripped
    assert len(vector) == 1536


def _run(coro):
    import asyncio

    return asyncio.run(coro)


# ── Ollama path: zero OpenAI usage ──────────────────────────────────────────
def test_embed_text_ollama_completes_with_zero_openai_calls(
    ollama_mode, no_openai_allowed, fake_http, monkeypatch
):
    fake_http.next_response = _FakeResponse(payload={"embeddings": [[0.5] * 768]})
    monkeypatch.setattr(settings, "ollama_embedding_dim", 768)

    vector = _run(embeddings.embed_text("revenue by region"))

    assert len(vector) == 768
    url, payload = fake_http.last_request
    assert url.endswith("/api/embed")
    assert payload == {"model": "nomic-embed-text", "input": ["revenue by region"]}


def test_embed_texts_ollama_preserves_order_and_batches(
    ollama_mode, no_openai_allowed, fake_http, monkeypatch
):
    monkeypatch.setattr(settings, "ollama_embedding_dim", 768)
    fake_http.next_response = _FakeResponse(
        payload={"embeddings": [[0.1] * 768, [0.2] * 768]}
    )

    vectors = _run(embeddings.embed_texts(["first", "second"]))

    assert len(vectors) == 2
    assert vectors[0][0] == 0.1 and vectors[1][0] == 0.2  # order preserved
    _url, payload = fake_http.last_request
    assert payload["input"] == ["first", "second"]  # one batched call


def test_ollama_normalization_matches_openai_path(
    ollama_mode, no_openai_allowed, fake_http, monkeypatch
):
    monkeypatch.setattr(settings, "ollama_embedding_dim", 768)
    fake_http.next_response = _FakeResponse(payload={"embeddings": [[0.5] * 768]})
    _run(embeddings.embed_text("what\n is\n revenue?"))
    _url, payload = fake_http.last_request
    assert payload["input"] == ["what  is  revenue?"]  # newlines folded, stripped


# ── Dimension verification (fail fast on model/config drift) ────────────────
def test_ollama_dim_mismatch_fails_fast_with_actionable_message(
    ollama_mode, no_openai_allowed, fake_http, monkeypatch
):
    monkeypatch.setattr(settings, "ollama_embedding_dim", 768)
    fake_http.next_response = _FakeResponse(payload={"embeddings": [[0.5] * 1024]})

    with pytest.raises(embeddings.EmbeddingProviderError) as excinfo:
        _run(embeddings.embed_text("q"))

    message = str(excinfo.value)
    assert "1024-dim" in message
    assert "OLLAMA_EMBEDDING_DIM" in message
    assert "reembed-stores" in message
    assert excinfo.value.provider == "ollama"


def test_ollama_dim_verified_once_then_cached(
    ollama_mode, no_openai_allowed, fake_http, monkeypatch
):
    monkeypatch.setattr(settings, "ollama_embedding_dim", 768)
    fake_http.next_response = _FakeResponse(payload={"embeddings": [[0.5] * 768]})
    _run(embeddings.embed_text("first"))
    assert embeddings._ollama_dim_verified is True
    # Second call still works (verification not repeated).
    vector = _run(embeddings.embed_text("second"))
    assert len(vector) == 768


# ── Provider failures are typed ──────────────────────────────────────────────
def test_unreachable_ollama_raises_typed_error(
    ollama_mode, no_openai_allowed, fake_http, monkeypatch
):
    monkeypatch.setattr(settings, "ollama_base_url", "http://localhost:11434")
    fake_http.raise_connect = httpx.ConnectError("connection refused")

    with pytest.raises(embeddings.EmbeddingProviderError) as excinfo:
        _run(embeddings.embed_text("q"))

    assert excinfo.value.provider == "ollama"
    assert "ollama serve" in str(excinfo.value)
    assert "ollama pull" in str(excinfo.value)  # names both fixes


def test_ollama_http_error_status_is_typed(ollama_mode, fake_http):
    fake_http.next_response = _FakeResponse(status_code=404, text="model not found")
    with pytest.raises(embeddings.EmbeddingProviderError) as excinfo:
        _run(embeddings.embed_text("q"))
    assert "HTTP 404" in str(excinfo.value)
    assert "ollama pull" in str(excinfo.value)


def test_ollama_malformed_payload_is_typed(ollama_mode, fake_http):
    fake_http.next_response = _FakeResponse(payload={"nope": True})
    with pytest.raises(embeddings.EmbeddingProviderError):
        _run(embeddings.embed_text("q"))


def test_ollama_wrong_embedding_count_is_typed(ollama_mode, fake_http):
    fake_http.next_response = _FakeResponse(payload={"embeddings": [[0.1] * 768]})
    with pytest.raises(embeddings.EmbeddingProviderError) as excinfo:
        _run(embeddings.embed_texts(["a", "b"]))
    assert "2 input(s)" in str(excinfo.value)


# ── Pure parser ──────────────────────────────────────────────────────────────
def test_parse_ollama_response_decodes_floats():
    response = _FakeResponse(payload={"embeddings": [[1, 2, 3]]})
    assert embeddings._parse_ollama_response(response, expected_count=1) == [[1.0, 2.0, 3.0]]


# ── Config: resolved dim + invalid provider fails fast ──────────────────────
def test_ollama_provider_resolves_configured_dim(monkeypatch):
    monkeypatch.setattr(settings, "embeddings_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_embedding_dim", 768)
    assert settings.embedding_dim == 768


def test_invalid_provider_is_rejected_at_settings_construction():
    with pytest.raises(ValidationError):
        Settings(embeddings_provider="vertex")


def test_invalid_provider_is_rejected_by_dim_property(monkeypatch):
    monkeypatch.setattr(settings, "embeddings_provider", "vertex")
    with pytest.raises(ValueError, match="EMBEDDINGS_PROVIDER"):
        _ = settings.embedding_dim

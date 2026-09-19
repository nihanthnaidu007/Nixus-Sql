"""Embeddings behind an EMBEDDINGS_PROVIDER knob (provider resilience).

Generation is Anthropic-only (every graph node uses ChatAnthropic), but the
retrieval layer embeds queries, schemas, and exemplars — historically via
OpenAI's text-embedding-3-small. This module keeps that path byte-identical
when ``EMBEDDINGS_PROVIDER=openai`` (the default) and adds an Ollama path
(``EMBEDDINGS_PROVIDER=ollama``) that serves ``{ollama_base_url}/api/embed``
— the no-OpenAI-key path for local demos.

Provider contract (verified against the live Ollama API this session, and
mirrored in docs/api.md): ``POST /api/embed`` with ``{"model": name,
"input": [texts]}`` returns ``{"embeddings": [[floats], ...]}`` — one vector
per input, input order preserved.

Failure semantics: provider-side failures raise :class:`EmbeddingProviderError`
(unconfigured credentials, unreachable endpoint, non-2xx response, dimension
mismatch) so the API layer can map them to a typed 503 envelope instead of a
raw 500. The OpenAI client is constructed lazily on first use — never at import
— so an ollama deployment with no OPENAI_API_KEY at all imports cleanly.
"""
import logging

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI

from nixus.config import settings
from nixus.utils.retry import embedding_retry

load_dotenv()

logger = logging.getLogger(__name__)

# OpenAI defaults — the historical provider, kept as module constants because
# tests and call sites import them.
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536


class EmbeddingProviderError(RuntimeError):
    """An embeddings-provider failure that is the PROVIDER's fault, not the app's.

    Carries the provider name so the API envelope can name it in the actionable
    fix. The API maps this (plus the openai/anthropic SDK failures) to a typed
    503 — never a raw 500.
    """

    def __init__(self, provider: str, message: str) -> None:
        self.provider = provider
        super().__init__(message)


# Constructed on first OPENAI call, never at import: eager construction made a
# missing OPENAI_API_KEY a crash of ANY process importing the app, and would
# break the ollama path (no key at all) before it ever ran. Existing tests that
# inject a fake client via ``_async_client`` keep working — a pre-set client is
# returned as-is.
_async_client: AsyncOpenAI | None = None


def _get_openai_client() -> AsyncOpenAI:
    global _async_client
    if _async_client is None:
        _async_client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _async_client


@embedding_retry
async def _create_embedding(text: str) -> list[float]:
    response = await _get_openai_client().embeddings.create(
        input=[text],
        model=EMBEDDING_MODEL,
    )
    return response.data[0].embedding


@embedding_retry
async def _create_embeddings(texts: list[str]) -> list[list[float]]:
    response = await _get_openai_client().embeddings.create(
        input=texts,
        model=EMBEDDING_MODEL,
    )
    return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]


# ── Ollama path ──────────────────────────────────────────────────────────────
# No retry wrapper (the openai/anthropic retryable-error set doesn't apply to a
# local HTTP endpoint): fail fast so the user sees the typed 503 with the fix
# instead of waiting out three backoff cycles on a service that is down.
_OLLAMA_TIMEOUT_S = 60.0  # first call loads the model into Ollama — can take seconds
_OLLAMA_HEALTH_TIMEOUT_S = 5.0

# Set once the model's native width has been verified against the configured
# expectation (per process; the check exists to catch a configuration drift,
# not per-call nondeterminism).
_ollama_dim_verified = False


async def _ollama_embeddings(texts: list[str]) -> list[list[float]]:
    """Batch-embed via the Ollama REST API (POST {base}/api/embed)."""
    url = f"{settings.ollama_base_url}/api/embed"
    try:
        async with httpx.AsyncClient(timeout=_OLLAMA_TIMEOUT_S) as client:
            response = await client.post(
                url,
                json={"model": settings.ollama_embedding_model, "input": texts},
            )
    except httpx.HTTPError as e:
        raise EmbeddingProviderError(
            "ollama",
            f"Could not reach Ollama at {settings.ollama_base_url} ({type(e).__name__}). "
            "Is it running (`ollama serve`) with the model pulled "
            f"(`ollama pull {settings.ollama_embedding_model}`)?",
        ) from e
    return _parse_ollama_response(response, expected_count=len(texts))


def _parse_ollama_response(
    response: httpx.Response, *, expected_count: int
) -> list[list[float]]:
    """Validate + decode the /api/embed response (pure, for tests).

    Raises EmbeddingProviderError on any deviation — never a partial or
    mis-shaped batch, and never a raw KeyError/TypeError deep in the
    retrieval pipeline.
    """
    if response.status_code != 200:
        raise EmbeddingProviderError(
            "ollama",
            f"Ollama returned HTTP {response.status_code} for model "
            f"{settings.ollama_embedding_model!r}: {response.text[:300]}. "
            f"If the model is missing, run `ollama pull {settings.ollama_embedding_model}`.",
        )
    try:
        payload = response.json()
        raw = payload["embeddings"]
    except Exception as e:
        raise EmbeddingProviderError(
            "ollama",
            "Ollama /api/embed response did not match the documented shape "
            "(expected {\"embeddings\": [[floats], ...]}): "
            f"{type(e).__name__}.",
        ) from e
    if not isinstance(raw, list) or len(raw) != expected_count:
        raise EmbeddingProviderError(
            "ollama",
            f"Ollama /api/embed returned {len(raw) if isinstance(raw, list) else 'non-list'} "
            f"embeddings for {expected_count} input(s) — unexpected provider response.",
        )
    vectors: list[list[float]] = []
    for vec in raw:
        if not isinstance(vec, list):
            raise EmbeddingProviderError(
                "ollama",
                "Ollama /api/embed returned a non-list embedding entry — "
                "unexpected provider response.",
            )
        vectors.append([float(x) for x in vec])
    return vectors


async def _verified_ollama_embeddings(texts: list[str]) -> list[list[float]]:
    """Ollama batch embed with the one-time model-native dimension check.

    pgvector columns are created at ``settings.embedding_dim``; a model whose
    native width disagrees would fail EVERY insert with a raw pgvector error
    that never names the fix. Verify the first real response instead.
    """
    global _ollama_dim_verified
    vectors = await _ollama_embeddings(texts)
    if not _ollama_dim_verified:
        actual = len(vectors[0]) if vectors else settings.ollama_embedding_dim
        expected = settings.ollama_embedding_dim
        if actual != expected:
            raise EmbeddingProviderError(
                "ollama",
                f"Ollama model {settings.ollama_embedding_model!r} returns {actual}-dim vectors, "
                f"but OLLAMA_EMBEDDING_DIM is {expected} (the width the vector stores were "
                f"sized for). Set OLLAMA_EMBEDDING_DIM={actual} to match the model, then run "
                "`nixus reembed-stores --yes` to rebuild the stores at that width.",
            )
        _ollama_dim_verified = True
    return vectors


async def check_ollama_reachable() -> bool:
    """Cheap metadata probe (GET {base}/api/tags) — no embedding cost, no tokens."""
    try:
        async with httpx.AsyncClient(timeout=_OLLAMA_HEALTH_TIMEOUT_S) as client:
            response = await client.get(f"{settings.ollama_base_url}/api/tags")
        return response.status_code == 200
    except httpx.HTTPError:
        return False


# ── Provider dispatch (the public API — unchanged call shape) ────────────────
async def embed_text(text: str) -> list[float]:
    """
    Embed a single text string using the active embeddings provider.
    Returns a list of ``settings.embedding_dim`` floats. Retries up to 3 times
    on transient OpenAI errors; re-raises on persistent failure (callers handle
    final failure). Provider outages raise EmbeddingProviderError.
    """
    text = text.replace("\n", " ").strip()[:20000]
    if settings.embeddings_provider == "ollama":
        vectors = await _verified_ollama_embeddings([text])
        return vectors[0]
    return await _create_embedding(text)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed multiple texts in a single batched call (order preserved).
    """
    texts = [t.replace("\n", " ").strip()[:20000] for t in texts]
    if settings.embeddings_provider == "ollama":
        return await _verified_ollama_embeddings(texts)
    return await _create_embeddings(texts)


# Backwards-compat alias for any caller still using the old sync name.
embed_batch = embed_texts

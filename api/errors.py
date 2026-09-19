"""Typed provider-failure envelope (provider resilience, deliverable 2).

Before this module, a provider outage (expired OpenAI key, unreachable Ollama,
rate-limited Anthropic) surfaced as the generic 500
``{"error": "An unexpected error occurred.", "trace_id": ...}`` — no provider
named, no fix offered. :func:`classify_provider_failure` maps the KNOWN
provider SDK failures (plus this repo's own EmbeddingProviderError) to a
503 envelope that names the provider and the actionable fix; everything else
stays on the generic 500 handler so genuinely unknown bugs still get the
full server-side traceback treatment.

Envelope shape (rendered verbatim by the frontend error box):

    {"error": "LLM provider unavailable",
     "detail": "Embedding provider 'openai' is not configured — set OPENAI_API_KEY or set EMBEDDINGS_PROVIDER=ollama",
     "provider": "openai",
     "trace_id": "a1b2c3d4"}
"""
from __future__ import annotations

import dataclasses
import json
import logging
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nixus.config import settings

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class ProviderFailure:
    provider: str
    detail: str


def _openai_detail(situation: str) -> str:
    return (
        f"Embedding provider 'openai' {situation} — set a valid OPENAI_API_KEY, "
        "or set EMBEDDINGS_PROVIDER=ollama to embed locally with Ollama."
    )


def _anthropic_detail(situation: str) -> str:
    return f"LLM provider 'anthropic' {situation} — verify ANTHROPIC_API_KEY is valid and has quota."


def classify_provider_failure(exc: BaseException) -> ProviderFailure | None:
    """Map a known provider failure to (provider, actionable detail).

    None → not a recognized provider failure; the caller falls back to the
    generic 500 path. Import-time raises inside are safe: the openai/anthropic
    packages are already hard dependencies of the graph nodes.
    """
    # Local embeddings: our own typed failure (unreachable endpoint, bad model,
    # dimension mismatch) already carries the provider name and the fix.
    from nixus.utils.embeddings import EmbeddingProviderError

    if isinstance(exc, EmbeddingProviderError):
        return ProviderFailure(provider=exc.provider, detail=str(exc))

    # openai SDK — today only the embeddings path talks to OpenAI.
    import openai

    if isinstance(exc, openai.AuthenticationError):
        return ProviderFailure("openai", _openai_detail("rejected the configured credentials (authentication failed)"))
    if isinstance(exc, openai.RateLimitError):
        return ProviderFailure("openai", _openai_detail("is rate limited — retry shortly"))
    if isinstance(exc, openai.APIConnectionError):
        return ProviderFailure("openai", _openai_detail("could not be reached (connection failure)"))
    if isinstance(exc, openai.InternalServerError):
        return ProviderFailure("openai", _openai_detail("is having an outage (5xx) — retry shortly"))
    if isinstance(exc, openai.OpenAIError):
        # Covers client-construction failures ("The api_key client option must
        # be set...") and any other SDK error not classified above.
        return ProviderFailure(
            "openai",
            "Embedding provider 'openai' is not configured — set OPENAI_API_KEY, "
            "or set EMBEDDINGS_PROVIDER=ollama to embed locally with Ollama.",
        )

    # langchain_anthropic surfaces Anthropic SDK errors uncaught — classify the
    # SDK types directly so generation-node failures get the same envelope.
    try:
        import anthropic
    except ImportError:  # pragma: no cover - anthropic is a hard dependency
        anthropic = None  # type: ignore[assignment]
    if anthropic is not None:
        if isinstance(exc, anthropic.AuthenticationError):
            return ProviderFailure("anthropic", _anthropic_detail("rejected the configured credentials (authentication failed)"))
        if isinstance(exc, anthropic.RateLimitError):
            return ProviderFailure("anthropic", _anthropic_detail("is rate limited — retry shortly"))
        if isinstance(exc, anthropic.APIConnectionError):
            return ProviderFailure("anthropic", _anthropic_detail("could not be reached (connection failure)"))
        if isinstance(exc, anthropic.InternalServerError):
            return ProviderFailure("anthropic", _anthropic_detail("is having an outage (5xx) — retry shortly"))

    # A pgvector dimension mismatch means stores were created under a different
    # provider width — route to the re-embed command, not to a generic 500.
    message = str(exc)
    if "dimensions" in message and "expected" in message:
        return ProviderFailure(
            "pgvector",
            f"Vector store dimension mismatch ({message.splitlines()[0][:200]}). "
            "The stores were built under a different embeddings provider — run "
            "`nixus reembed-stores --yes` to rebuild them at the active provider's width.",
        )
    return None


def provider_failure_payload(failure: ProviderFailure, trace_id: str) -> dict[str, str]:
    """The 503 envelope body (error stream events reuse the same shape)."""
    return {
        "error": "LLM provider unavailable",
        "detail": failure.detail,
        "provider": failure.provider,
        "trace_id": trace_id,
    }


def embeddings_provider_for_health() -> str:
    """Active embeddings provider name for health payloads (normalized)."""
    return settings.embeddings_provider



# ---------------------------------------------------------------------------
# HTTP delivery of the envelopes.
#
# ROOT CAUSE (browser "Failed to fetch"): responses produced by the generic
# `Exception` handler (api/main.py) are sent by ServerErrorMiddleware — the
# OUTERMOST ASGI layer, outside CORSMiddleware — so they reach browsers WITHOUT
# `access-control-allow-origin`. The browser blocks the response body and the
# fetch rejects with TypeError "Failed to fetch": the typed envelope is
# produced but never delivered. Classification must therefore run INSIDE the
# CORS layer, where every response — 503 or 500 — leaves with CORS headers.
# ---------------------------------------------------------------------------


class ProviderEnvelopeMiddleware:
    """Pure ASGI middleware delivering typed envelopes for route-level errors.

    Sits inside CORSMiddleware (registered before it — ``add_middleware``
    prepends), so envelope responses pick up CORS headers and browsers can
    actually read them. Classification reuses :func:`classify_provider_failure`:
    a known provider failure answers 503 with the actionable envelope; anything
    else keeps the generic 500 body (unchanged shape, now CORS-deliverable).

    Pure ASGI (not BaseHTTPMiddleware) so streaming responses — the /stream SSE
    endpoint — pass through with no buffering, mirroring APIKeyMiddleware.

    Once the response has STARTED (an SSE body is flowing), an exception can no
    longer be converted — status and headers are already on the wire — so the
    exception re-raises and the stream generator's own error-event handling
    (api/main.py) governs, as it did before this middleware existed.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            if response_started:
                raise
            trace_id = str(uuid.uuid4())[:8]

            failure = classify_provider_failure(exc)
            if failure is not None:
                logger.error(
                    f"[{trace_id}] Provider failure on {scope['method']} "
                    f"{scope['path']}: {type(exc).__name__}: {failure.detail}"
                )
                status, body = 503, provider_failure_payload(failure, trace_id)
            else:
                logger.error(
                    f"[{trace_id}] Unhandled exception on {scope['method']} "
                    f"{scope['path']}: {type(exc).__name__}: {exc}",
                    exc_info=exc,
                )
                status, body = 500, generic_500_payload(exc, trace_id)

            payload = json.dumps(body).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(payload)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": payload})


def generic_500_payload(exc: Exception, trace_id: str) -> dict[str, str]:
    """The generic 500 body (shared by the middleware and the backstop handler)."""
    return {
        "error": "An unexpected error occurred.",
        "trace_id": trace_id,
        "type": type(exc).__name__,
    }

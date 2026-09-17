"""API-key authentication for the NIXUS SQL API (Wave 0 security blocker).

Before this module, every /api route was open: anyone who could reach the API
could burn Anthropic/OpenAI quota via /run and /stream, execute arbitrary
SELECTs via /run-sql, and mutate the cache via /cache-evict.

One middleware now guards every ``/api`` route except the health probe. The key
comes from the ``API_KEY`` environment variable (``nixus.config.settings``) and
is read at request time, so rotating it or running tests needs no restart.

Fail-closed by design: with no real key configured the middleware answers 503
with setup guidance on every protected route — the API never silently opens.
Exposed as a pure decision function (``check_access``) plus a thin pure-ASGI
wrapper, so the rules are unit-testable without HTTP and SSE streams pass
through without buffering.
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass

from starlette.types import ASGIApp, Receive, Scope, Send

from nixus.config import is_placeholder, settings

# The only intentionally unauthenticated /api path: the health alias infra
# probes use (load balancers, uptime checks, container healthchecks).
HEALTH_PATH = "/api/health"

SETUP_GUIDANCE = (
    "Set API_KEY in the environment (.env), then restart the API. Clients must "
    "send it as the X-API-Key header on every /api request except /api/health. "
    "The API refuses to serve until a key is configured."
)

_UNAUTHORIZED_BODY = {
    "error": "Unauthorized.",
    "detail": "A valid X-API-Key header is required on /api routes.",
}

_SETUP_BODY = {
    "error": "API key is not configured.",
    "detail": SETUP_GUIDANCE,
}


@dataclass(frozen=True)
class Rejection:
    """A blocked request: the status and JSON body to answer with."""

    status: int
    body: dict


def configured_api_key() -> str | None:
    """The configured key, or None when unset/placeholder (fail-closed read)."""
    key = settings.api_key
    if key is None or is_placeholder(key):
        return None
    return key.strip()


def check_access(
    path: str,
    method: str,
    supplied_key: str | None,
    configured_key: str | None,
) -> Rejection | None:
    """Pure auth decision for one request.

    Returns None to allow the request through, or a :class:`Rejection` with the
    status + body to answer with. Rules, in order:

    1. Only ``/api`` routes are guarded at all; ``OPTIONS`` passes through so
       CORS preflights are answered by the CORS middleware, not by auth.
    2. The health probe (``/api/health``) is exempt — infra probes must be able
       to see the service even while it is misconfigured.
    3. No configured key → 503 fail-closed with setup guidance (never open).
    4. Wrong/missing supplied key → 401, compared with ``secrets.compare_digest``.
    """
    if not path.startswith("/api") or method == "OPTIONS" or path == HEALTH_PATH:
        return None
    if configured_key is None:
        return Rejection(status=503, body=dict(_SETUP_BODY))
    if not supplied_key or not secrets.compare_digest(
        supplied_key.encode("utf-8"), configured_key.encode("utf-8")
    ):
        return Rejection(status=401, body=dict(_UNAUTHORIZED_BODY))
    return None


class APIKeyMiddleware:
    """Pure ASGI middleware applying :func:`check_access` to every http request.

    Pure ASGI (not BaseHTTPMiddleware) so streaming responses — the /stream SSE
    endpoint — pass through with no buffering.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        rejection = check_access(
            path=scope.get("path", ""),
            method=scope.get("method", ""),
            supplied_key=headers.get("x-api-key"),
            configured_key=configured_api_key(),
        )
        if rejection is None:
            await self.app(scope, receive, send)
            return

        payload = json.dumps(rejection.body).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": rejection.status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})

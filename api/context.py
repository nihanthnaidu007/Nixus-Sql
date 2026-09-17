"""Per-request context for the API adapter.

``RequestContext`` carries the identity that flows through a single API request.
Today that is just the session id; it is intentionally minimal. When auth lands
(Phase 8), authenticated-user identity will be added here as NEW fields without
reshaping handler signatures — handlers already receive the context instead of
reaching into the raw request ad hoc, so the extension point is in one place.

Deliberately absent: ``tenant_id``. V1 has no multi-tenancy — that is a V2
concern, addable later via a migration. Do not add a tenant field here.

This type lives in the API adapter only. The framework-agnostic core (``nixus/``)
never imports it; handlers pass plain values (e.g. ``ctx.session_id``) across the
boundary, so rule 1 (one-way dependency direction) stays intact.

Note: session identity is resolved by ``api.sessions.resolve_session_id`` (the
server issues ids on first use and binds them in the ``api_sessions`` registry),
and the API key arrives in the ``X-API-Key`` header (``api/auth.py``). This type
remains the extension point for future per-request identity fields (e.g. an
authenticated user id); when that lands it can become a FastAPI ``Depends``
provider without changing the handlers.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class RequestContext:
    """Immutable per-request identity. session_id only in V1."""

    session_id: str

    @classmethod
    def for_session(cls, session_id: str | None) -> RequestContext:
        """Build a context, generating a session id when the caller didn't supply one.

        Mirrors the previous inline ``req.session_id or str(uuid.uuid4())`` exactly.
        """
        return cls(session_id=session_id or str(uuid.uuid4()))

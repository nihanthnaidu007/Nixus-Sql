"""Server-issued session ids (Wave 0 security blocker).

The session id doubles as the LangGraph checkpoint ``thread_id``
(``nixus.services.query_service.get_thread_config``), so before this module a
client-supplied ``session_id`` selected ANY checkpoint thread directly — one
client could resume or inspect another session's conversation state.

Now the server is the only id issuer:

- absent/empty ``session_id`` → the server issues a fresh uuid, records it in
  the state DB (``api_sessions``), and returns it in the response for the
  client to reuse on follow-ups;
- a supplied id this server never issued → :class:`UnknownSessionError`, which
  the API answers as 404.

"Recorded in ``api_sessions``" is therefore exactly the set of checkpoint
threads any client can reach — unforgeable, because only the server INSERTs.

This lives in the API adapter (the core never imports it); the DB registry it
uses is ordinary state-DB bookkeeping in ``nixus.db.session_store``.
"""
from __future__ import annotations

import uuid

from nixus.db.session_store import register_session, session_exists


class UnknownSessionError(Exception):
    """A client-supplied session_id was never issued by this server."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        super().__init__(f"Unknown session_id: {session_id}")


def new_session_id() -> str:
    """A fresh server-issued session id."""
    return str(uuid.uuid4())


async def resolve_session_id(supplied: str | None) -> str:
    """Issue on first use; validate + bind when supplied.

    Empty/whitespace input (the fresh-single-turn case, and what the React UI
    sends on a new conversation) issues and registers a new id. A non-empty id
    must already be in the registry — anything else raises
    :class:`UnknownSessionError` (the API answers 404).
    """
    candidate = (supplied or "").strip()
    if not candidate:
        fresh = new_session_id()
        await register_session(fresh)
        return fresh
    if not await session_exists(candidate):
        raise UnknownSessionError(candidate)
    return candidate

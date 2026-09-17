from dotenv import load_dotenv

load_dotenv()


from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nixus.config import settings

# ─────────────────────────────────────────────────────────────────────────────
# TWO independent connections (split in 2.1):
#
#   state  -> STATE_DATABASE_URL  : NIXUS-owned bookkeeping, READ-WRITE
#             (schema_embeddings, fewshot_examples, query_cache,
#              schema_migrations, and the LangGraph checkpointer tables)
#
#   target -> TARGET_DATABASE_URL : the user's data, STRICTLY READ-ONLY
#             (the generated SQL is executed here through a Postgres role that
#              holds only SELECT — read-only is enforced by Postgres, not by us)
#
# Both reuse the exact same asyncpg/SQLAlchemy pattern. The only difference is
# the URL (and therefore the role) each one points at. Call sites pick a side
# explicitly via the `state_engine` / `target_engine` objects (or the
# get_state_engine() / get_target_engine() accessors) so routing is unambiguous.
# ─────────────────────────────────────────────────────────────────────────────


def _to_async_url(url: str) -> str:
    """Rewrite a plain ``postgresql://`` URL to the asyncpg driver form and strip
    the ``sslmode=require`` query param (asyncpg takes SSL via connect_args)."""
    if url.startswith("postgresql://"):
        async_url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        async_url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    else:
        async_url = url

    if "?sslmode=require" in async_url:
        async_url = async_url.replace("?sslmode=require", "")
    elif "&sslmode=require" in async_url:
        async_url = async_url.replace("&sslmode=require", "")
    return async_url


def _connect_args(url: str) -> dict:
    # Neon requires SSL; asyncpg passes it via connect_args, not the URL.
    return {"ssl": "require"} if "neon.tech" in url else {}


# ── State connection (NIXUS-owned, READ-WRITE) ──────────────────────────────

STATE_DATABASE_URL = settings.state_url
if not STATE_DATABASE_URL:
    raise RuntimeError(
        "STATE_DATABASE_URL (or legacy DATABASE_URL) environment variable not set. "
        "Copy .env.example to .env and set your state database URL."
    )

state_engine: AsyncEngine = create_async_engine(
    _to_async_url(STATE_DATABASE_URL),
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    connect_args=_connect_args(STATE_DATABASE_URL),
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    state_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ── Target connection (user data, STRICTLY READ-ONLY) ───────────────────────
# Created with the read-only role's credentials from TARGET_DATABASE_URL. Used
# ONLY to execute the generated SQL (and, in 2.2, to introspect). Built lazily
# tolerant of an unset URL so importing this module never hard-fails when only
# state is configured; get_target_engine() raises a clear error if used unset.

TARGET_DATABASE_URL = settings.target_url

target_engine: AsyncEngine | None = (
    create_async_engine(
        _to_async_url(TARGET_DATABASE_URL),
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        connect_args=_connect_args(TARGET_DATABASE_URL),
        echo=False,
    )
    if TARGET_DATABASE_URL
    else None
)


def get_state_engine() -> AsyncEngine:
    """The READ-WRITE state engine (NIXUS-owned bookkeeping)."""
    return state_engine


def get_target_engine() -> AsyncEngine:
    """The READ-ONLY target engine (the user's data).

    Raises if TARGET_DATABASE_URL is not configured, so a missing target is a
    loud failure at the call site rather than a silent fall-through to state.
    """
    if target_engine is None:
        raise RuntimeError(
            "TARGET_DATABASE_URL environment variable not set. "
            "The target (read-only) database must be configured to execute the "
            "generated SQL. See .env.example."
        )
    return target_engine


async def check_db_connection() -> bool:
    """Async health check on the STATE database — used by FastAPI /api/health."""
    try:
        async with state_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def check_target_connection() -> bool:
    """Async health check on the TARGET (read-only) database."""
    if target_engine is None:
        return False
    try:
        async with target_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


# ── Sync engines (psycopg2) — used ONLY by seed scripts, init_db, and eval ──
# asyncpg: async driver for the main application
# psycopg2-binary: sync driver for seed scripts / the eval harness only
#
# sync_engine        -> state  (NIXUS-owned, read-write): migrations check, eval
#                        liveness probe, cache cleanup in latency tests.
# sync_target_engine -> target (read-only): executing gold SQL in the eval
#                        harness, which reads the user's data.

sync_engine = create_engine(
    STATE_DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)

sync_target_engine = (
    create_engine(
        TARGET_DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )
    if TARGET_DATABASE_URL
    else None
)

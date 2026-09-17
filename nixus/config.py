"""Single configuration source for NIXUS SQL (introduced in 1.1e).

This object centralizes environment reads that were previously scattered as
ad-hoc ``os.environ`` / ``os.getenv`` calls across the core and the API
adapter. Every field default, type, and parsing tolerance mirrors the
pre-consolidation behavior EXACTLY as of 1.1e — this is a *consolidation*, not
a *hardening*. No default was added, dropped, or tightened, and no validation
was introduced that would reject a value the old loose code accepted.

Defaults reflect the raw reads:
  - ``os.environ.get("X")``        -> Optional field defaulting to ``None``.
  - ``os.environ.get("X", "v")``   -> field defaulting to ``"v"`` (cast to the
                                      type the old call site cast to).
  - The single effectively-required value (``DATABASE_URL``) is mirrored as a
    raw ``Optional[str]`` here; its required-ness is enforced exactly as before
    by ``nixus.db.connection``'s own ``RuntimeError`` guard, which is preserved.

Intentionally NOT folded in (adapter-process-local, not shared app config):
  - the Streamlit UI's ``API_BASE_URL`` (ui/app.py),
  - the eval harness's ``NIXUS_API_URL`` / ``NIXUS_METRICS_FILE`` (eval/conftest.py),
  - the retired Chinook migration script's vars (scripts/migrate_chinook.py).
See the 1.1e report for the rationale.
"""
import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def is_placeholder(value: str | None) -> bool:
    """True when a credential is empty or still an .env.example sentinel.

    The template ships sentinels shaped ``your_<name>_here`` —
    ``your_anthropic_api_key_here``, ``your_openai_api_key_here``,
    ``your_langsmith_key_here``. Any empty/whitespace value or such a sentinel
    means "not configured": no API call should be attempted with it, and the
    health endpoint must report the provider as NOT connected. Deliberately
    small and explicit rather than a clever regex.
    """
    if value is None:
        return True
    v = value.strip()
    if not v:
        return True
    return v.startswith("your_") and v.endswith("_here")


class Settings(BaseSettings):
    # Read from the process environment and from a .env file if present, matching
    # the project's existing python-dotenv (`load_dotenv()`) behavior. `extra`
    # is ignored so the many .env keys that are not modeled here (LANGCHAIN_*,
    # CHINOOK_*, API_BASE_URL, ...) do not raise. case_sensitive=False keeps the
    # uppercase env names matching the lowercase field names, as os.environ did.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Database (split into two connections in 2.1) ────────────────────────
    # NIXUS now talks to TWO databases:
    #   - state_db  (READ-WRITE): NIXUS-owned bookkeeping — schema_embeddings,
    #     fewshot_examples, query_cache, schema_migrations, and the LangGraph
    #     checkpointer tables.
    #   - target_db (STRICTLY READ-ONLY): the user's data. The generated SQL is
    #     executed here through a Postgres role that holds only SELECT.
    #
    # ``STATE_DATABASE_URL`` is the canonical name; the historical
    # ``DATABASE_URL`` is still honored as a fallback so existing .env files keep
    # working. ``state_url`` resolves the two. Both default to None (the bare
    # read); ``nixus.db.connection`` enforces required-ness via its RuntimeError
    # guard, exactly as before.
    state_database_url: str | None = Field(default=None)   # STATE_DATABASE_URL
    database_url: str | None = Field(default=None)         # DATABASE_URL (legacy → state)

    # Read-only handle the APP uses for the target database. Never written to.
    target_database_url: str | None = Field(default=None)  # TARGET_DATABASE_URL

    # Writable OWNER connection to the target database, used ONLY by the one-time
    # Chinook seed / provisioning scripts (never by the app at runtime). The app's
    # only handle to the target is the read-only ``target_database_url`` above.
    target_admin_database_url: str | None = Field(default=None)  # TARGET_ADMIN_DATABASE_URL

    # ── LLM API keys ────────────────────────────────────────────────────────
    # Reads varied across sites: .get("ANTHROPIC_API_KEY") -> None and
    # .get("ANTHROPIC_API_KEY", "") -> "". Field mirrors the bare read (None);
    # the two ""-defaulting sites apply `or ""` to stay byte-identical.
    anthropic_api_key: str | None = Field(default=None)
    openai_api_key: str | None = Field(default=None)

    # ── Retrieval / correction tuning (int/float casts mirrored into the type) ─
    max_correction_attempts: int = Field(default=3)        # MAX_CORRECTION_ATTEMPTS
    schema_retrieval_top_k: int = Field(default=6)         # SCHEMA_RETRIEVAL_TOP_K
    fewshot_retrieval_top_k: int = Field(default=3)        # FEWSHOT_RETRIEVAL_TOP_K
    fewshot_similarity_threshold: float = Field(default=0.60)   # FEWSHOT_SIMILARITY_THRESHOLD
    cache_similarity_threshold: float = Field(default=0.92)     # CACHE_SIMILARITY_THRESHOLD
    query_timeout_ms: int = Field(default=30000)           # QUERY_TIMEOUT_MS
    pie_max_slices: int = Field(default=6)                 # PIE_MAX_SLICES
    # Max distinct values a SECOND categorical may have to be drawn as multi-series
    # (one line per value, or grouped bars). Above this the split is unreadable
    # noise, so classify_chart falls back to a single series. ~8 keeps the legend
    # legible while still covering real splits (e.g. plan tiers).
    multi_series_max: int = Field(default=8)               # MULTI_SERIES_MAX

    # ── Cache eviction (read at API startup) ────────────────────────────────
    cache_max_age_days: int = Field(default=30)            # CACHE_MAX_AGE_DAYS
    cache_max_entries: int = Field(default=10000)          # CACHE_MAX_ENTRIES

    # Seed fewshot_examples from the benchmark corpus at API startup when the
    # store is empty (cold start). Disable with FEWSHOT_SEED_ON_STARTUP=false.
    fewshot_seed_on_startup: bool = Field(default=True)    # FEWSHOT_SEED_ON_STARTUP

    # ── API: CORS + health ──────────────────────────────────────────────────
    # Old read a raw string and split on "," with strip + empty-filter applied
    # at the call site. Kept as a raw string here so the call site can apply the
    # exact same split; a Pydantic list field would coerce commas differently
    # (and reject some previously-accepted input). future: could be a typed list.
    allowed_origins: str = Field(default="http://localhost:8501,http://localhost:3000")
    llm_health_cache_ttl: int = Field(default=300)         # LLM_HEALTH_CACHE_TTL

    # ── API authentication (X-API-Key) ──────────────────────────────────────
    # The shared key every /api client must send as the X-API-Key header (the
    # health probe is exempt). Unset or placeholder → the API FAILS CLOSED: it
    # answers 503 with setup guidance on every protected route instead of
    # silently opening. Checked per-request so tests and rotations need no restart.
    api_key: str | None = Field(default=None)           # API_KEY

    # ── Logging ─────────────────────────────────────────────────────────────
    # Old applied .upper(); the call site keeps .upper() to stay identical.
    log_level: str = Field(default="INFO")                 # LOG_LEVEL

    # ── LangSmith observability ─────────────────────────────────────────────
    # Old truthiness was strictly `.lower() == "true"`, which is STRICTER than
    # Pydantic's bool coercion (which would treat "1"/"yes"/"on"/"t" as True).
    # To preserve exact behavior we keep the raw string and expose
    # `tracing_enabled` replicating the old test. Do NOT change this to a bool
    # field — that would accept inputs the old code treated as False.
    langchain_tracing_v2: str = Field(default="false")     # LANGCHAIN_TRACING_V2
    langchain_project: str = Field(default="nixus-sql")    # LANGCHAIN_PROJECT
    langchain_api_key: str | None = Field(default=None)  # LANGCHAIN_API_KEY

    @property
    def tracing_enabled(self) -> bool:
        """Whether LangSmith tracing is actually active.

        The raw flag still uses the strict ``.lower() == "true"`` test (NOT
        Pydantic bool coercion — see the field note above). On top of that, a
        placeholder/empty ``LANGCHAIN_API_KEY`` forces tracing OFF: enabling the
        tracer with the .env.example sentinel produces a 403 on every LLM call
        and floods the logs (7.2 amendment, defect D). Tracing is therefore
        opt-in: a REAL key AND ``LANGCHAIN_TRACING_V2=true``.
        """
        if is_placeholder(self.langchain_api_key):
            return False
        return self.langchain_tracing_v2.lower() == "true"

    # ── Resolved DB URLs ────────────────────────────────────────────────────
    @property
    def state_url(self) -> str | None:
        """The state (NIXUS-owned, read-write) DB URL.

        Prefers ``STATE_DATABASE_URL``; falls back to the legacy ``DATABASE_URL``
        so existing .env files keep working unchanged.
        """
        return self.state_database_url or self.database_url

    @property
    def target_url(self) -> str | None:
        """The target (user data, READ-ONLY) DB URL used by the app."""
        return self.target_database_url

    @property
    def target_admin_url(self) -> str | None:
        """Writable OWNER URL to the target DB — bootstrap/seed scripts only."""
        return self.target_admin_database_url


# Single module-level instance imported by every call site. Reads the
# environment once at import, matching the project's prior import-time reads.
settings = Settings()


def apply_tracing_gate() -> None:
    """Force LangSmith tracing OFF in the process environment when no real key
    is present (7.2 amendment, defect D).

    LangChain's background tracer reads ``LANGCHAIN_TRACING_V2`` straight from
    ``os.environ``, not from this Settings object. If the .env ships
    ``LANGCHAIN_TRACING_V2=true`` alongside the placeholder ``LANGCHAIN_API_KEY``
    sentinel, the tracer activates and 403s on every LLM call. Overwriting the
    env var here — at config import, before any LLM call — guarantees the tracer
    stays dormant unless a real key is configured. No-op when a real key exists.
    """
    if is_placeholder(settings.langchain_api_key):
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        # keep the parsed setting consistent so `tracing_enabled` agrees.
        settings.langchain_tracing_v2 = "false"


# Apply the gate at import so it runs before langchain initializes its tracer.
apply_tracing_gate()

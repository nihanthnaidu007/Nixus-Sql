"""
Structured logging configuration for NIXUS SQL.
All log lines use a consistent format so they can be parsed by
log aggregators (Railway, Datadog, CloudWatch, etc.).
"""
import logging
import time

from nixus.config import settings

LOG_LEVEL = settings.log_level.upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"nixus_sql.{name}")


def log_query_start(
    logger: logging.Logger,
    session_id: str,
    user_query: str,
) -> float:
    """Log query start and return start timestamp."""
    start = time.monotonic()
    logger.info(
        f"[{session_id[:8]}] QUERY_START | query={user_query[:80]!r}"
    )
    return start


def log_query_complete(
    logger: logging.Logger,
    session_id: str,
    user_query: str,
    intent_class: str,
    cache_hit: bool,
    corrections_used: int,
    result_quality: str,
    row_count: int,
    chart_type: str | None,
    duration_ms: float,
    error: str | None = None,
):
    """Log query completion with all key metrics on a single structured line."""
    status = "ERROR" if error else "OK"
    logger.info(
        f"[{session_id[:8]}] QUERY_COMPLETE | "
        f"status={status} | "
        f"intent={intent_class} | "
        f"cache_hit={cache_hit} | "
        f"corrections={corrections_used} | "
        f"quality={result_quality} | "
        f"rows={row_count} | "
        f"chart={chart_type or 'none'} | "
        f"duration_ms={duration_ms:.0f} | "
        f"query={user_query[:60]!r}"
        + (f" | error={error[:100]!r}" if error else "")
    )


def log_node_event(
    logger: logging.Logger,
    session_id: str,
    node_name: str,
    event: str,
    detail: str = "",
):
    """Log individual node start/complete events at DEBUG level."""
    logger.debug(
        f"[{session_id[:8]}] NODE_{event.upper()} | node={node_name}"
        + (f" | {detail}" if detail else "")
    )

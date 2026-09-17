"""Guarded result-set export (Phase 2, Wave 1 D1).

Executes a SELECT against the TARGET database under the SAME guardrails as the
pipeline's execute node — statement timeout plus the ``ROW_FETCH_LIMIT`` row
cap — and serializes the result to CSV, XLSX, or JSON. Exports never widen the
existing ceiling: a capped export exports the capped set and LABELS the cap
(``capped`` in the payload / ``X-Nixus-Capped`` response header), it never
re-fetches beyond the limit.

Framework-agnostic: no FastAPI imports — the API layer owns HTTP concerns
(content types, Content-Disposition). The serializers are pure functions.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from nixus.db.connection import get_target_engine

# The GENERATED SQL runs against the user's data → TARGET database, through
# the read-only role — the same single source of truth for the ceiling as the
# pipeline's execute node.
from nixus.graph.nodes.execute_query import QUERY_TIMEOUT_MS, ROW_FETCH_LIMIT


@dataclass(frozen=True)
class GuardedResult:
    """One capped, read-only result set ready for serialization."""

    columns: list
    rows: list
    row_count: int
    row_limit: int
    capped: bool


class ExportQueryError(Exception):
    """The guarded SELECT failed at execution (bad column, timeout, …)."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


async def execute_guarded(sql: str) -> GuardedResult:
    """Run one read-only SELECT under the pipeline's guardrails.

    Fetches one row BEYOND the cap purely to detect truncation honestly: if
    the extra row exists the result is labeled capped and trimmed to the cap.
    """
    try:
        async with get_target_engine().connect() as conn:
            async with conn.begin():
                await conn.execute(
                    text(f"SET LOCAL statement_timeout = '{QUERY_TIMEOUT_MS}ms'")
                )
                result = await conn.execute(text(sql))
                # LIMIT+1 probe: the extra row proves more data exists.
                fetched = [dict(r._mapping) for r in result.fetchmany(ROW_FETCH_LIMIT + 1)]
                columns = list(result.keys())
    except SQLAlchemyError as e:
        message = str(e)
        if "statement timeout" in message.lower() or "canceling statement" in message.lower():
            message = f"Query exceeded {QUERY_TIMEOUT_MS}ms timeout. Try a more specific query."
        raise ExportQueryError(message) from e

    capped = len(fetched) > ROW_FETCH_LIMIT
    rows = fetched[:ROW_FETCH_LIMIT]
    return GuardedResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        row_limit=ROW_FETCH_LIMIT,
        capped=capped,
    )


def _cell(value):
    """One DB value → a serializable spreadsheet cell.

    openpyxl accepts int/float/str/datetime/bool; Decimal lands via str so the
    sheet keeps the exact text the user saw. None becomes an empty cell.
    """
    if value is None:
        return ""
    if isinstance(value, bool | int | float | str):
        return value
    return str(value)


def to_csv_bytes(columns: list, rows: list) -> bytes:
    """RFC-4180 CSV, UTF-8 with BOM so Excel renders non-ASCII columns."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(row.get(c)) for c in columns])
    return buf.getvalue().encode("utf-8-sig")


def to_xlsx_bytes(columns: list, rows: list) -> bytes:
    """One sheet: header row + values. Rows beyond the cap never reach here."""
    from openpyxl import Workbook

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("export")
    ws.append([str(c) for c in columns])
    for row in rows:
        ws.append([_cell(row.get(c)) for c in columns])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_json_bytes(columns: list, rows: list, row_limit: int, capped: bool) -> bytes:
    """JSON payload carrying the cap label inline (headers for CSV/XLSX)."""
    payload = {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "row_limit": row_limit,
        "capped": capped,
        "exported_at": datetime.now().astimezone().isoformat(),
    }
    return json.dumps(payload, default=str, indent=2).encode("utf-8")


_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str | None, extension: str) -> str:
    """A download filename the browser can trust: sanitized stem + correct
    extension (replacing, never appending after, any user-supplied suffix)."""
    stem = (name or "").strip()
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    stem = _SANITIZE_RE.sub("_", stem).strip("._")
    if not stem:
        stem = "nixus-export-" + datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    return f"{stem}.{extension}"

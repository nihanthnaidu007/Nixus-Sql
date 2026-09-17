"""Export endpoints (Phase 2, Wave 1 D1) — CSV / XLSX / JSON result exports.

Every route sits under the app-wide ``APIKeyMiddleware``, so a request without
a valid key never reaches a handler (fail-closed, inherited — no per-route
auth code).

Guardrails are the SERVICE's, not this handler's: the SELECT runs through
``nixus.services.export_service.execute_guarded`` (read-only role, statement
timeout, ``ROW_FETCH_LIMIT`` row cap). A capped export returns the capped set
and carries ``X-Nixus-Capped`` / ``X-Nixus-Row-Limit`` headers so the client
can label it — it never silently pretends to be complete. The JSON payload
additionally carries ``capped`` inline.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from nixus.services.export_service import (
    ExportQueryError,
    GuardedResult,
    execute_guarded,
    safe_filename,
    to_csv_bytes,
    to_json_bytes,
    to_xlsx_bytes,
)
from nixus.utils.sql_safety import is_read_only_sql

router = APIRouter(prefix="/export", tags=["export"])

# Content types are exact per format (tests assert them): RFC-4180 CSV is
# text/csv, XLSX is the OOXML spreadsheet type, JSON is application/json.
_MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json",
}


class ExportRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=100_000)
    # Optional download-name stem; the extension is always derived from the
    # format so the browser can never receive a lying file type.
    name: str | None = Field(default=None, max_length=200)


def _serialize(fmt: str, result: GuardedResult) -> bytes:
    if fmt == "csv":
        return to_csv_bytes(result.columns, result.rows)
    if fmt == "xlsx":
        return to_xlsx_bytes(result.columns, result.rows)
    return to_json_bytes(result.columns, result.rows, result.row_limit, result.capped)


def _content_disposition(filename: str) -> str:
    """ASCII-safe attachment header; non-ASCII names fall back to ``export``."""
    try:
        filename.encode("ascii")
        return f'attachment; filename="{filename}"'
    except UnicodeEncodeError:
        return 'attachment; filename="export"'


async def _export_response(sql: str, name: str | None, fmt: str) -> Response:
    """Shared guardrail path for all three formats."""
    is_safe, reason = is_read_only_sql(sql)
    if not is_safe:
        raise HTTPException(
            status_code=400,
            detail={"error": "Only SELECT statements are permitted.", "detail": reason},
        )
    try:
        result = await execute_guarded(sql)
    except ExportQueryError as e:
        raise HTTPException(status_code=400, detail={"error": e.message}) from e

    headers = {"X-Nixus-Row-Limit": str(result.row_limit)}
    if result.capped:
        headers["X-Nixus-Capped"] = "true"
    return Response(
        content=_serialize(fmt, result),
        media_type=_MEDIA_TYPES[fmt],
        headers={
            **headers,
            "Content-Disposition": _content_disposition(safe_filename(name, fmt)),
        },
    )


@router.post("/csv")
async def export_csv(req: ExportRequest):
    return await _export_response(req.sql, req.name, "csv")


@router.post("/xlsx")
async def export_xlsx(req: ExportRequest):
    return await _export_response(req.sql, req.name, "xlsx")


@router.post("/json")
async def export_json(req: ExportRequest):
    return await _export_response(req.sql, req.name, "json")

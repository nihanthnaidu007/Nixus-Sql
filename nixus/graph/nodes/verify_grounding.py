"""verify_grounding node — the trust backbone.

Runs AFTER ``validate_syntax`` (so the SQL is known to parse) and BEFORE
``execute_query``. It checks that the SQL references only tables/columns that
actually exist, using the LIVE introspected schema as the authoritative source —
the retrieved top-k schema context omits tables, so it cannot be trusted to rule
a table "unknown" without risking a false positive.

This node is intentionally thin (rule 4): all grounding logic lives in the pure,
unit-tested ``nixus.graph.grounding`` module. On a hallucination it emits the
SAME structured error shape ``self_correct`` consumes (``validation_result`` with
``is_valid=False``) and routes back into the EXISTING self-correction loop; no new
refusal path is invented.

Fails OPEN: if the authoritative schema cannot be fetched we cannot verify, so we
PASS (the governing rule — never reject valid SQL on our own uncertainty).
"""
from datetime import datetime

from nixus.db.connection import get_target_engine
from nixus.graph.grounding import (
    SchemaView,
    check_grounding,
    schema_view_from_introspection,
)
from nixus.graph.state import SQLAgentState
from nixus.schema.introspect import introspect_schema

# The target schema is stable for the life of the process (re-embedding/drift are
# handled out of band), so introspect once and reuse — avoids a catalog round-trip
# per query without risking staleness within a run.
_schema_view_cache: SchemaView | None = None


def now():
    return datetime.now().astimezone().strftime("%H:%M:%S")


async def _get_schema_view() -> SchemaView:
    global _schema_view_cache
    if _schema_view_cache is None:
        schema = await introspect_schema(get_target_engine())
        _schema_view_cache = schema_view_from_introspection(schema)
    return _schema_view_cache


async def verify_grounding_node(state: SQLAgentState) -> SQLAgentState:
    state["current_node"] = "verify_grounding"
    # Ground the exact SQL that will execute (the normalized form), falling back
    # to the generated SQL. validate_syntax(valid) guarantees normalized_sql here.
    sql = (state.get("validation_result") or {}).get("normalized_sql") or state["generated_sql"]

    try:
        view = await _get_schema_view()
    except Exception as e:
        # Cannot reach the authoritative schema → cannot verify → PASS (fail open).
        state["grounding_result"] = {
            "is_grounded": True, "checked": False,
            "hallucinated_tables": [], "hallucinated_columns": [],
            "message": f"Grounding skipped (schema unavailable: {type(e).__name__})",
        }
        state["stream_updates"].append({
            "timestamp": now(), "node": "verify_grounding",
            "message": "Grounding skipped — schema unavailable", "status": "done",
        })
        state["completed_nodes"].append("verify_grounding")
        return state

    result = check_grounding(sql, view)
    state["grounding_result"] = {
        "is_grounded": result.is_grounded,
        "checked": result.checked,
        "hallucinated_tables": result.hallucinated_tables,
        "hallucinated_columns": result.hallucinated_columns,
        "message": result.message,
    }

    if result.is_grounded:
        state["stream_updates"].append({
            "timestamp": now(), "node": "verify_grounding",
            "message": ("Grounding OK — all identifiers exist in schema"
                        if result.checked else "Grounding skipped (unparsed SQL)"),
            "status": "done",
        })
    else:
        # Route to the EXISTING self_correct loop via the structured error shape it
        # consumes. self_correct picks its failure reason from execution_result →
        # result_quality → validation_result; clear the first two so the grounding
        # message (carried in validation_result) is what it diagnoses and rewrites.
        state["validation_result"] = {
            "is_valid": False,
            "errors": [result.message],
            "warnings": [],
            "normalized_sql": sql,
        }
        state["execution_result"] = None
        state["result_quality"] = None
        state["stream_updates"].append({
            "timestamp": now(), "node": "verify_grounding",
            "message": f"Grounding FAILED: {result.message[:140]}",
            "status": "error",
        })

    state["completed_nodes"].append("verify_grounding")
    return state

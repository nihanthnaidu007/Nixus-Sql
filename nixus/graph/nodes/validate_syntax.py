from datetime import datetime

import sqlglot
from sqlglot.errors import ParseError

from nixus.graph.state import SQLAgentState


def now():
    return datetime.now().astimezone().strftime("%H:%M:%S")


async def validate_syntax_node(state: SQLAgentState) -> SQLAgentState:
    state["current_node"] = "validate_syntax"
    sql = state["generated_sql"]
    errors, warnings = [], []

    if sql.strip().upper() == "CANNOT_ANSWER":
        state["validation_result"] = {
            "is_valid": False,
            "errors": ["LLM determined this query cannot be answered from available schema"],
            "warnings": [],
            "normalized_sql": sql,
        }
        state["error"] = "This question cannot be answered from the available database schema."
        state["completed_nodes"].append("validate_syntax")
        state["stream_updates"].append({
            "timestamp": now(), "node": "validate_syntax",
            "message": "Cannot answer from schema",
            "status": "error",
        })
        return state

    try:
        parsed = sqlglot.parse_one(sql, dialect="postgres")
        normalized = parsed.sql(dialect="postgres", pretty=True)

        if "SELECT *" in sql.upper():
            warnings.append("SELECT * detected — consider selecting specific columns for performance")
        if any(k in sql.upper() for k in ["UPDATE ", "DELETE "]) and "WHERE" not in sql.upper():
            warnings.append("Write operation without WHERE clause — may affect all rows")

        state["validation_result"] = {
            "is_valid": True, "errors": [], "warnings": warnings, "normalized_sql": normalized,
        }
        state["stream_updates"].append({
            "timestamp": now(), "node": "validate_syntax",
            "message": "Syntax valid" + (f" ({len(warnings)} warnings)" if warnings else ""),
            "status": "done",
        })
    except ParseError as e:
        errors = [str(e)]
        state["validation_result"] = {
            "is_valid": False, "errors": errors, "warnings": [], "normalized_sql": sql,
        }
        state["stream_updates"].append({
            "timestamp": now(), "node": "validate_syntax",
            "message": f"Syntax ERROR: {errors[0][:120]}",
            "status": "error",
        })

    state["completed_nodes"].append("validate_syntax")
    return state

from datetime import datetime

# Single source of truth: the cap is enforced by the execute node's
# fetchmany() — a local copy here could silently drift from it (the exact
# hazard api/guardrails.py:20-24 warns about).
from nixus.graph.nodes.execute_query import ROW_FETCH_LIMIT
from nixus.graph.state import SQLAgentState


def now():
    return datetime.now().astimezone().strftime("%H:%M:%S")


async def check_result_node(state: SQLAgentState) -> SQLAgentState:
    state["current_node"] = "check_result"
    result = state["execution_result"]
    # The graph only reaches this node after execute_query, which always sets
    # execution_result — the assert makes that invariant visible to mypy.
    assert result is not None, "check_result before execute_query: no execution_result"

    if not result["success"]:
        quality = {"status": "ERROR", "reasoning": result["error"], "is_acceptable": False}
    elif result["row_count"] == 0:
        # Successful execution with no rows: still route to classify_chart / UI table
        # (self_correct rarely fixes true empty datasets and burned correction budget).
        quality = {
            "status": "EMPTY",
            "reasoning": (
                "Zero rows returned. The SQL ran successfully — causes may include restrictive "
                "filters, joins that eliminate rows, or empty tables (e.g. demo data not loaded)."
            ),
            "is_acceptable": True,
        }
    elif result["row_count"] >= ROW_FETCH_LIMIT:
        quality = {
            "status": "OVERFLOW",
            "reasoning": (
                "Query returned the maximum row limit (1000). "
                "Results may be incomplete. Consider adding "
                "a more specific filter or LIMIT clause."
            ),
            "is_acceptable": True,
        }
    else:
        quality = {"status": "GOOD", "reasoning": "Result looks valid.", "is_acceptable": True}

    state["result_quality"] = quality
    state["completed_nodes"].append("check_result")
    state["stream_updates"].append({
        "timestamp": now(), "node": "check_result",
        "message": f"Result quality: {quality['status']} — {result['row_count']} rows",
        "status": "done" if quality["is_acceptable"] else "error",
    })
    return state

from datetime import datetime

from nixus.config import settings
from nixus.db.schema_store import search_schemas
from nixus.graph.state import SQLAgentState
from nixus.utils.embeddings import embed_text

SCHEMA_TOP_K = settings.schema_retrieval_top_k


def now():
    return datetime.now().astimezone().strftime("%H:%M:%S")


async def retrieve_schema_node(state: SQLAgentState) -> SQLAgentState:
    state["current_node"] = "retrieve_schema"
    embedding = await embed_text(state["user_query"])
    schemas = await search_schemas(embedding, limit=SCHEMA_TOP_K)

    context_lines = []
    for s in schemas:
        context_lines.append(
            f"TABLE: {s['table_name']}\n"
            f"DESCRIPTION: {s['description']}\n"
            f"COLUMNS: {s['columns_json']}\n"
            f"SAMPLES: {s['sample_values_json'] or 'N/A'}\n"
        )

    state["relevant_schemas"] = schemas
    state["schema_context"] = "\n---\n".join(context_lines)
    state["tables_identified"] = [s["table_name"] for s in schemas]
    state["completed_nodes"].append("retrieve_schema")
    state["stream_updates"].append({
        "timestamp": now(), "node": "retrieve_schema",
        "message": f"Schema loaded: {state['tables_identified']} ({len(schemas)} tables)",
        "status": "done",
    })
    return state

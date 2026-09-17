from datetime import datetime

from nixus.config import settings
from nixus.db.fewshot_store import search_fewshots
from nixus.graph.state import SQLAgentState
from nixus.utils.embeddings import embed_text

FEWSHOT_TOP_K = settings.fewshot_retrieval_top_k
FEWSHOT_THRESHOLD = settings.fewshot_similarity_threshold


def now():
    return datetime.now().astimezone().strftime("%H:%M:%S")


async def retrieve_fewshot_node(state: SQLAgentState) -> SQLAgentState:
    state["current_node"] = "retrieve_fewshot"
    embedding = await embed_text(state["user_query"])
    examples = await search_fewshots(embedding, limit=FEWSHOT_TOP_K, threshold=FEWSHOT_THRESHOLD)

    context_lines = []
    for i, ex in enumerate(examples, 1):
        context_lines.append(
            f"EXAMPLE {i} (type: {ex['query_type']}, similarity: {ex['similarity']:.2f}):\n"
            f"Question: {ex['natural_language']}\n"
            f"SQL:\n{ex['sql_query']}"
        )

    state["similar_examples"] = examples
    state["fewshot_context"] = (
        "\n\n".join(context_lines)
        if context_lines
        else "No similar examples found — generate from schema alone."
    )
    state["completed_nodes"].append("retrieve_fewshot")
    state["stream_updates"].append({
        "timestamp": now(), "node": "retrieve_fewshot",
        "message": f"Few-shot loaded: {len(examples)} examples (types: {[e['query_type'] for e in examples]})",
        "status": "done",
    })
    return state

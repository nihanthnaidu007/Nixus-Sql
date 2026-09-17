from datetime import datetime

from nixus.config import settings
from nixus.db.query_cache import increment_hit_count, search_cache
from nixus.graph.state import SQLAgentState
from nixus.utils.embeddings import embed_text

CACHE_HIT_THRESHOLD = settings.cache_similarity_threshold


def now():
    return datetime.now().astimezone().strftime("%H:%M:%S")


async def check_cache_node(state: SQLAgentState) -> SQLAgentState:
    state["current_node"] = "check_cache"
    embedding = await embed_text(state["user_query"])
    result = await search_cache(embedding, threshold=CACHE_HIT_THRESHOLD)

    if result:
        await increment_hit_count(result["id"])
        state["cache_result"] = {
            "hit": True,
            "similarity": result["similarity"],
            "cached_sql": result["generated_sql"],
            "result_preview": result["result_preview"],
            "row_count": result["row_count"],
            "chart_type": result["chart_type"],
            "explanation": result["explanation"],
            "cache_id": result["id"],
        }
        state["served_from_cache"] = True
        state["generated_sql"] = result["generated_sql"]
        state["explanation"] = result["explanation"]
        state["stream_updates"].append({
            "timestamp": now(), "node": "check_cache",
            "message": f"Cache HIT (similarity: {result['similarity']:.3f}) — serving cached result",
            "status": "done",
        })
    else:
        state["cache_result"] = {"hit": False, "similarity": 0.0}
        state["served_from_cache"] = False
        state["stream_updates"].append({
            "timestamp": now(), "node": "check_cache",
            "message": "Cache MISS — proceeding to schema retrieval",
            "status": "done",
        })

    state["completed_nodes"].append("check_cache")
    return state

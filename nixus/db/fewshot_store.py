from sqlalchemy import text

# fewshot_examples is NIXUS-owned bookkeeping (read + LEARN) → STATE database.
from nixus.db.connection import state_engine


async def search_fewshots(
    embedding: list,
    limit: int = 3,
    threshold: float = 0.60,
) -> list:
    """Async pgvector similarity search on fewshot_examples.

    THE RETRIEVAL GATE for the reject invariant: explicitly rejected examples
    (disabled = TRUE) are never re-served — filtered here once, not patched
    into each corpus writer.
    """
    vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
    async with state_engine.connect() as conn:
        rows = await conn.execute(text("""
            SELECT
                natural_language,
                sql_query,
                tables_used,
                query_type,
                1 - (embedding <=> CAST(:query AS vector)) AS similarity
            FROM fewshot_examples
            WHERE 1 - (embedding <=> CAST(:query AS vector)) >= :threshold
              AND NOT disabled
            ORDER BY embedding <=> CAST(:query AS vector)
            LIMIT :limit
        """), {
            "query": vec_str,
            "threshold": threshold,
            "limit": limit,
        })
        results = rows.fetchall()

    return [
        {
            "natural_language": r[0],
            "sql_query": r[1],
            "tables_used": list(r[2]) if r[2] else [],
            "query_type": r[3],
            "similarity": float(r[4]),
        }
        for r in results
    ]


async def _is_duplicate(
    embedding: list,
    threshold: float = 0.98,
) -> bool:
    """Return True if an existing fewshot example has cosine similarity
    >= `threshold` with the given query embedding.

    0.98 is intentionally strict: only near-exact rephrasings are blocked,
    not semantically similar but distinct queries.
    """
    vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
    async with state_engine.connect() as conn:
        result = await conn.execute(
            text("""
                SELECT 1
                FROM fewshot_examples
                WHERE 1 - (embedding <=> CAST(:emb AS vector)) >= :threshold
                  AND NOT disabled
                LIMIT 1
            """),
            {"emb": vec_str, "threshold": threshold},
        )
        return result.fetchone() is not None


async def store_fewshot_example(
    natural_language: str,
    sql_query: str,
    tables_used: list,
    auto_learned: bool = False,
) -> int | None:
    """Store a new few-shot example with embedding; return its id.

    Returns None if skipped due to near-duplicate detection (cosine
    similarity >= 0.98 against a live, non-disabled row). The id is the
    run→few-shot linkage: callers stamp it into the run state so the query
    history row can name the corpus row a later rejection would demote.

    Provenance (``source``) is derived from ``auto_learned`` — the flag every
    writer already passes — so all four writers keep one call shape.
    """
    from nixus.utils.embeddings import embed_text
    embedding = await embed_text(natural_language)

    if await _is_duplicate(embedding):
        return None

    vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
    query_type = _infer_query_type(sql_query)

    async with state_engine.begin() as conn:
        result = await conn.execute(text("""
            INSERT INTO fewshot_examples
                (natural_language, sql_query, tables_used,
                 query_type, embedding, auto_learned, source)
            VALUES
                (:nl, :sql, :tables, :qtype,
                 CAST(:emb AS vector), :auto,
                 CASE WHEN :auto THEN 'auto' ELSE 'seed' END)
            RETURNING id
        """), {
            "nl": natural_language,
            "sql": sql_query,
            "tables": tables_used,
            "qtype": query_type,
            "emb": vec_str,
            "auto": auto_learned,
        })
        return int(result.scalar_one())


async def disable_fewshot_example(fewshot_id: int) -> bool:
    """Tombstone one example (explicit reject): never deleted, never re-served.

    Idempotent. The tombstone — not a hard DELETE — preserves the audit trail;
    retrieval and duplicate checks filter disabled rows, so rejecting also
    un-blocks re-learning the corrected query (a 0.98 near-duplicate would
    otherwise pin the bad SQL in the corpus forever).
    """
    async with state_engine.begin() as conn:
        result = await conn.execute(
            text("UPDATE fewshot_examples SET disabled = TRUE WHERE id = :id"),
            {"id": fewshot_id},
        )
        return result.rowcount > 0


def _infer_query_type(sql: str) -> str:
    upper = sql.upper()
    if any(k in upper for k in ["OVER (", "PARTITION BY", "ROW_NUMBER", "RANK(", "DENSE_RANK", "LAG(", "LEAD("]):
        return "window"
    if upper.count("SELECT") > 1 or "WITH " in upper:
        return "subquery"
    if "JOIN" in upper:
        return "join"
    if any(k in upper for k in ["COUNT(", "SUM(", "AVG(", "MAX(", "MIN(", "GROUP BY"]):
        return "aggregation"
    return "filter"


async def get_fewshot_stats() -> dict:
    async with state_engine.connect() as conn:
        row = await conn.execute(text("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN auto_learned THEN 1 ELSE 0 END) AS learned,
                SUM(CASE WHEN NOT auto_learned THEN 1 ELSE 0 END) AS seeded
            FROM fewshot_examples
        """))
        r = row.fetchone()
        # A bare aggregate (COUNT/SUM, no GROUP BY) always returns exactly one row.
        assert r is not None
    return {
        "total": r[0] or 0,
        "auto_learned": r[1] or 0,
        "seeded": r[2] or 0,
    }

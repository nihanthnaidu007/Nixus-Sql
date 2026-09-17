from sqlalchemy import text

# schema_embeddings is NIXUS-owned bookkeeping → STATE database (read-write).
from nixus.db.connection import state_engine


async def search_schemas(embedding: list, limit: int = 6) -> list:
    """Async pgvector similarity search on schema_embeddings."""
    vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
    async with state_engine.connect() as conn:
        rows = await conn.execute(text("""
            SELECT
                table_name,
                description,
                columns_json,
                sample_values_json,
                1 - (embedding <=> CAST(:query AS vector)) AS similarity
            FROM schema_embeddings
            ORDER BY embedding <=> CAST(:query AS vector)
            LIMIT :limit
        """), {"query": vec_str, "limit": limit})
        results = rows.fetchall()

    return [
        {
            "table_name": r[0],
            "description": r[1],
            "columns_json": r[2],
            "sample_values_json": r[3],
            "similarity": float(r[4]),
        }
        for r in results
    ]


async def store_schema_embedding(
    table_name: str,
    description: str,
    columns_json: str,
    sample_values_json: str,
    embedding: list,
) -> None:
    """Store or update a schema embedding."""
    vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
    async with state_engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO schema_embeddings
                (table_name, description, columns_json,
                 sample_values_json, embedding)
            VALUES
                (:table_name, :description, :columns_json,
                 :sample_values_json, CAST(:embedding AS vector))
            ON CONFLICT (table_name) DO UPDATE SET
                description       = EXCLUDED.description,
                columns_json      = EXCLUDED.columns_json,
                sample_values_json = EXCLUDED.sample_values_json,
                embedding         = EXCLUDED.embedding
        """), {
            "table_name": table_name,
            "description": description,
            "columns_json": columns_json,
            "sample_values_json": sample_values_json,
            "embedding": vec_str,
        })


async def get_schema_count() -> int:
    async with state_engine.connect() as conn:
        result = await conn.execute(text("SELECT COUNT(*) FROM schema_embeddings"))
        return result.scalar() or 0


async def list_schema_rows() -> list[dict]:
    """Return the currently embedded structure (table_name + columns_json) for
    every schema_embeddings row. Used by drift detection — no embeddings read."""
    async with state_engine.connect() as conn:
        rows = await conn.execute(text(
            "SELECT table_name, columns_json FROM schema_embeddings ORDER BY table_name"
        ))
        return [{"table_name": r[0], "columns_json": r[1]} for r in rows.fetchall()]


async def replace_schema_embeddings(rows: list[dict]) -> int:
    """Full wipe-and-rebuild of schema_embeddings in a SINGLE transaction.

    Clears EVERY existing schema_embeddings row, then inserts the given set, so a
    failure mid-rebuild rolls back to the prior populated store rather than
    leaving it half-written. (Wipe scope: the entire table — acceptable while V1
    embeds a single target.)

    Each ``row`` dict: table_name, description, columns_json, sample_values_json
    (may be None), embedding (list[float]). Returns the number of rows written.
    """
    async with state_engine.begin() as conn:
        await conn.execute(text("DELETE FROM schema_embeddings"))
        for r in rows:
            vec_str = "[" + ",".join(str(v) for v in r["embedding"]) + "]"
            await conn.execute(text("""
                INSERT INTO schema_embeddings
                    (table_name, description, columns_json, sample_values_json, embedding)
                VALUES
                    (:table_name, :description, :columns_json,
                     :sample_values_json, CAST(:embedding AS vector))
            """), {
                "table_name": r["table_name"],
                "description": r["description"],
                "columns_json": r["columns_json"],
                "sample_values_json": r.get("sample_values_json"),
                "embedding": vec_str,
            })
    return len(rows)


async def retrieve_relevant_schemas(query: str, top_k: int = 6) -> list:
    """Embed the natural-language query and run a similarity search.

    Convenience wrapper used by retrieve_schema_node and ad-hoc tooling
    that wants to go from query string → ranked tables in one call.
    """
    from nixus.utils.embeddings import embed_text

    embedding = await embed_text(query)
    return await search_schemas(embedding, limit=top_k)

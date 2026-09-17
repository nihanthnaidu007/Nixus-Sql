"""
Seed 25 golden few-shot Q-SQL examples covering 5 query types.
--skip-if-exists: if fewshot_examples count > 0, exit early.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from nixus.db.connection import sync_engine as engine
from nixus.utils.embeddings import embed_texts

EXAMPLES = [
    # AGGREGATION (5)
    {
        "natural_language": "What are the top 5 artists by total track count?",
        "sql_query": """SELECT ar."Name" AS artist_name, COUNT(t."TrackId") AS track_count
FROM "Artist" ar
JOIN "Album" al ON ar."ArtistId" = al."ArtistId"
JOIN "Track" t ON al."AlbumId" = t."AlbumId"
GROUP BY ar."ArtistId", ar."Name"
ORDER BY track_count DESC
LIMIT 5""",
        "tables_used": ["Artist", "Album", "Track"],
        "query_type": "aggregation"
    },
    {
        "natural_language": "Show total invoice amount per country, highest first",
        "sql_query": """SELECT i."BillingCountry" AS country, ROUND(SUM(i."Total"), 2) AS total_revenue
FROM "Invoice" i
GROUP BY i."BillingCountry"
ORDER BY total_revenue DESC
LIMIT 1000""",
        "tables_used": ["Invoice"],
        "query_type": "aggregation"
    },
    {
        "natural_language": "What is the average track length by genre?",
        "sql_query": """SELECT g."Name" AS genre, ROUND(AVG(t."Milliseconds") / 1000.0, 1) AS avg_seconds
FROM "Genre" g
JOIN "Track" t ON g."GenreId" = t."GenreId"
GROUP BY g."GenreId", g."Name"
ORDER BY avg_seconds DESC
LIMIT 1000""",
        "tables_used": ["Genre", "Track"],
        "query_type": "aggregation"
    },
    {
        "natural_language": "How many customers are in each country?",
        "sql_query": """SELECT "Country", COUNT("CustomerId") AS customer_count
FROM "Customer"
WHERE "Country" IS NOT NULL
GROUP BY "Country"
ORDER BY customer_count DESC
LIMIT 1000""",
        "tables_used": ["Customer"],
        "query_type": "aggregation"
    },
    {
        "natural_language": "What is the total revenue per sales rep employee?",
        "sql_query": """SELECT e."FirstName" || ' ' || e."LastName" AS sales_rep,
       ROUND(SUM(i."Total"), 2) AS total_revenue,
       COUNT(i."InvoiceId") AS invoice_count
FROM "Employee" e
JOIN "Customer" c ON e."EmployeeId" = c."SupportRepId"
JOIN "Invoice" i ON c."CustomerId" = i."CustomerId"
GROUP BY e."EmployeeId", e."FirstName", e."LastName"
ORDER BY total_revenue DESC
LIMIT 1000""",
        "tables_used": ["Employee", "Customer", "Invoice"],
        "query_type": "aggregation"
    },
    # JOIN (5)
    {
        "natural_language": "List all tracks with their album name and artist name",
        "sql_query": """SELECT t."Name" AS track_name, al."Title" AS album_title, ar."Name" AS artist_name
FROM "Track" t
JOIN "Album" al ON t."AlbumId" = al."AlbumId"
JOIN "Artist" ar ON al."ArtistId" = ar."ArtistId"
ORDER BY ar."Name", al."Title", t."Name"
LIMIT 1000""",
        "tables_used": ["Track", "Album", "Artist"],
        "query_type": "join"
    },
    {
        "natural_language": "Show customer names and their total purchase amounts",
        "sql_query": """SELECT c."FirstName" || ' ' || c."LastName" AS customer_name,
       c."Country",
       ROUND(SUM(i."Total"), 2) AS total_spent
FROM "Customer" c
JOIN "Invoice" i ON c."CustomerId" = i."CustomerId"
GROUP BY c."CustomerId", c."FirstName", c."LastName", c."Country"
ORDER BY total_spent DESC
LIMIT 1000""",
        "tables_used": ["Customer", "Invoice"],
        "query_type": "join"
    },
    {
        "natural_language": "Which playlists contain the most tracks?",
        "sql_query": """SELECT p."Name" AS playlist_name, COUNT(pt."TrackId") AS track_count
FROM "Playlist" p
JOIN "PlaylistTrack" pt ON p."PlaylistId" = pt."PlaylistId"
GROUP BY p."PlaylistId", p."Name"
ORDER BY track_count DESC
LIMIT 1000""",
        "tables_used": ["Playlist", "PlaylistTrack"],
        "query_type": "join"
    },
    {
        "natural_language": "Show each invoice with customer name and billing country",
        "sql_query": """SELECT i."InvoiceId", c."FirstName" || ' ' || c."LastName" AS customer_name,
       i."BillingCountry", i."InvoiceDate"::DATE AS invoice_date, i."Total"
FROM "Invoice" i
JOIN "Customer" c ON i."CustomerId" = c."CustomerId"
ORDER BY i."InvoiceDate" DESC
LIMIT 1000""",
        "tables_used": ["Invoice", "Customer"],
        "query_type": "join"
    },
    {
        "natural_language": "List tracks with their genre and media type",
        "sql_query": """SELECT t."Name" AS track_name, g."Name" AS genre, mt."Name" AS media_type,
       ROUND(t."Milliseconds" / 60000.0, 2) AS duration_minutes
FROM "Track" t
JOIN "Genre" g ON t."GenreId" = g."GenreId"
JOIN "MediaType" mt ON t."MediaTypeId" = mt."MediaTypeId"
ORDER BY t."Name"
LIMIT 1000""",
        "tables_used": ["Track", "Genre", "MediaType"],
        "query_type": "join"
    },
    # FILTER (5)
    {
        "natural_language": "Find all customers from Germany",
        "sql_query": """SELECT "CustomerId", "FirstName", "LastName", "City", "Email"
FROM "Customer"
WHERE "Country" ILIKE 'Germany'
ORDER BY "LastName"
LIMIT 1000""",
        "tables_used": ["Customer"],
        "query_type": "filter"
    },
    {
        "natural_language": "Which tracks are longer than 5 minutes?",
        "sql_query": """SELECT t."Name" AS track_name, ROUND(t."Milliseconds" / 60000.0, 2) AS duration_minutes
FROM "Track" t
WHERE t."Milliseconds" > 300000
ORDER BY t."Milliseconds" DESC
LIMIT 1000""",
        "tables_used": ["Track"],
        "query_type": "filter"
    },
    {
        "natural_language": "Show invoices from 2009",
        "sql_query": """SELECT "InvoiceId", "CustomerId", "InvoiceDate"::DATE AS invoice_date, "BillingCountry", "Total"
FROM "Invoice"
WHERE EXTRACT(YEAR FROM "InvoiceDate") = 2009
ORDER BY "InvoiceDate"
LIMIT 1000""",
        "tables_used": ["Invoice"],
        "query_type": "filter"
    },
    {
        "natural_language": "Find employees hired after 2003",
        "sql_query": """SELECT "EmployeeId", "FirstName" || ' ' || "LastName" AS name, "Title", "HireDate"::DATE AS hire_date
FROM "Employee"
WHERE "HireDate" > '2003-01-01'
ORDER BY "HireDate"
LIMIT 1000""",
        "tables_used": ["Employee"],
        "query_type": "filter"
    },
    {
        "natural_language": "Which tracks cost more than $1.00?",
        "sql_query": """SELECT t."Name" AS track_name, t."UnitPrice", g."Name" AS genre
FROM "Track" t
JOIN "Genre" g ON t."GenreId" = g."GenreId"
WHERE t."UnitPrice" > 1.00
ORDER BY t."UnitPrice" DESC
LIMIT 1000""",
        "tables_used": ["Track", "Genre"],
        "query_type": "filter"
    },
    # SUBQUERY (5)
    {
        "natural_language": "Which artists have more than 10 albums?",
        "sql_query": """SELECT ar."Name" AS artist_name, album_counts.album_count
FROM "Artist" ar
JOIN (
    SELECT "ArtistId", COUNT(*) AS album_count
    FROM "Album"
    GROUP BY "ArtistId"
    HAVING COUNT(*) > 10
) album_counts ON ar."ArtistId" = album_counts."ArtistId"
ORDER BY album_counts.album_count DESC
LIMIT 1000""",
        "tables_used": ["Artist", "Album"],
        "query_type": "subquery"
    },
    {
        "natural_language": "Find customers who have spent more than the average customer",
        "sql_query": """SELECT c."FirstName" || ' ' || c."LastName" AS customer_name,
       c."Country", ROUND(SUM(i."Total"), 2) AS total_spent
FROM "Customer" c
JOIN "Invoice" i ON c."CustomerId" = i."CustomerId"
GROUP BY c."CustomerId", c."FirstName", c."LastName", c."Country"
HAVING SUM(i."Total") > (
    SELECT AVG(customer_total)
    FROM (
        SELECT SUM("Total") AS customer_total
        FROM "Invoice"
        GROUP BY "CustomerId"
    ) sub
)
ORDER BY total_spent DESC
LIMIT 1000""",
        "tables_used": ["Customer", "Invoice"],
        "query_type": "subquery"
    },
    {
        "natural_language": "Which genres have no tracks longer than 4 minutes?",
        "sql_query": """SELECT g."Name" AS genre
FROM "Genre" g
WHERE g."GenreId" NOT IN (
    SELECT DISTINCT "GenreId"
    FROM "Track"
    WHERE "Milliseconds" > 240000
      AND "GenreId" IS NOT NULL
)
ORDER BY g."Name"
LIMIT 1000""",
        "tables_used": ["Genre", "Track"],
        "query_type": "subquery"
    },
    {
        "natural_language": "Show tracks that appear in more than 3 playlists",
        "sql_query": """SELECT t."Name" AS track_name, playlist_counts.playlist_count
FROM "Track" t
JOIN (
    SELECT "TrackId", COUNT(*) AS playlist_count
    FROM "PlaylistTrack"
    GROUP BY "TrackId"
    HAVING COUNT(*) > 3
) playlist_counts ON t."TrackId" = playlist_counts."TrackId"
ORDER BY playlist_counts.playlist_count DESC
LIMIT 1000""",
        "tables_used": ["Track", "PlaylistTrack"],
        "query_type": "subquery"
    },
    {
        "natural_language": "Which employees support more than 5 customers?",
        "sql_query": """SELECT e."FirstName" || ' ' || e."LastName" AS employee_name,
       e."Title", customer_counts.customer_count
FROM "Employee" e
JOIN (
    SELECT "SupportRepId", COUNT(*) AS customer_count
    FROM "Customer"
    WHERE "SupportRepId" IS NOT NULL
    GROUP BY "SupportRepId"
    HAVING COUNT(*) > 5
) customer_counts ON e."EmployeeId" = customer_counts."SupportRepId"
ORDER BY customer_counts.customer_count DESC
LIMIT 1000""",
        "tables_used": ["Employee", "Customer"],
        "query_type": "subquery"
    },
    # WINDOW (5)
    {
        "natural_language": "Rank artists by total revenue within each genre",
        "sql_query": """SELECT g."Name" AS genre,
       ar."Name" AS artist_name,
       ROUND(SUM(il."UnitPrice" * il."Quantity"), 2) AS total_revenue,
       RANK() OVER (PARTITION BY g."GenreId" ORDER BY SUM(il."UnitPrice" * il."Quantity") DESC) AS revenue_rank
FROM "Genre" g
JOIN "Track" t ON g."GenreId" = t."GenreId"
JOIN "Album" al ON t."AlbumId" = al."AlbumId"
JOIN "Artist" ar ON al."ArtistId" = ar."ArtistId"
JOIN "InvoiceLine" il ON t."TrackId" = il."TrackId"
GROUP BY g."GenreId", g."Name", ar."ArtistId", ar."Name"
ORDER BY genre, revenue_rank
LIMIT 1000""",
        "tables_used": ["Genre", "Track", "Album", "Artist", "InvoiceLine"],
        "query_type": "window"
    },
    {
        "natural_language": "Show running total of invoice amounts by date",
        "sql_query": """SELECT "InvoiceDate"::DATE AS invoice_date,
       "Total",
       ROUND(SUM("Total") OVER (ORDER BY "InvoiceDate" ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), 2) AS running_total
FROM "Invoice"
ORDER BY "InvoiceDate"
LIMIT 1000""",
        "tables_used": ["Invoice"],
        "query_type": "window"
    },
    {
        "natural_language": "Rank customers by purchase amount within their country",
        "sql_query": """SELECT c."Country",
       c."FirstName" || ' ' || c."LastName" AS customer_name,
       ROUND(SUM(i."Total"), 2) AS total_spent,
       RANK() OVER (PARTITION BY c."Country" ORDER BY SUM(i."Total") DESC) AS country_rank
FROM "Customer" c
JOIN "Invoice" i ON c."CustomerId" = i."CustomerId"
GROUP BY c."CustomerId", c."FirstName", c."LastName", c."Country"
ORDER BY c."Country", country_rank
LIMIT 1000""",
        "tables_used": ["Customer", "Invoice"],
        "query_type": "window"
    },
    {
        "natural_language": "Show each track's length relative to average for its genre",
        "sql_query": """SELECT t."Name" AS track_name,
       g."Name" AS genre,
       ROUND(t."Milliseconds" / 1000.0, 1) AS duration_seconds,
       ROUND(AVG(t."Milliseconds") OVER (PARTITION BY g."GenreId") / 1000.0, 1) AS genre_avg_seconds,
       ROUND((t."Milliseconds" - AVG(t."Milliseconds") OVER (PARTITION BY g."GenreId")) / 1000.0, 1) AS diff_from_avg
FROM "Track" t
JOIN "Genre" g ON t."GenreId" = g."GenreId"
ORDER BY ABS(t."Milliseconds" - AVG(t."Milliseconds") OVER (PARTITION BY g."GenreId")) DESC
LIMIT 1000""",
        "tables_used": ["Track", "Genre"],
        "query_type": "window"
    },
    {
        "natural_language": "What is each customer's most recent invoice date?",
        "sql_query": """SELECT c."FirstName" || ' ' || c."LastName" AS customer_name,
       c."Country",
       MAX(i."InvoiceDate")::DATE AS most_recent_invoice,
       FIRST_VALUE(i."Total") OVER (PARTITION BY c."CustomerId" ORDER BY i."InvoiceDate" DESC) AS most_recent_total
FROM "Customer" c
JOIN "Invoice" i ON c."CustomerId" = i."CustomerId"
GROUP BY c."CustomerId", c."FirstName", c."LastName", c."Country", i."InvoiceDate", i."Total"
ORDER BY most_recent_invoice DESC
LIMIT 1000""",
        "tables_used": ["Customer", "Invoice"],
        "query_type": "window"
    }
]


def main():
    if "--skip-if-exists" in sys.argv:
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM fewshot_examples")).scalar()
        if count and count > 0:
            print(f"◈ Few-shot examples already seeded ({count} examples). Skipping.")
            return

    print(f"◈ Seeding {len(EXAMPLES)} few-shot examples...")
    texts = [ex["natural_language"] for ex in EXAMPLES]
    embeddings = asyncio.run(embed_texts(texts))

    vec_sql = text("""
        INSERT INTO fewshot_examples (natural_language, sql_query, tables_used, query_type, embedding, auto_learned)
        VALUES (:nl, :sql, :tables, :qtype, CAST(:emb AS vector), FALSE)
    """)

    with engine.connect() as conn:
        for i, (ex, emb) in enumerate(zip(EXAMPLES, embeddings)):
            vec_str = "[" + ",".join(str(v) for v in emb) + "]"
            conn.execute(vec_sql, {
                "nl": ex["natural_language"],
                "sql": ex["sql_query"],
                "tables": ex["tables_used"],
                "qtype": ex["query_type"],
                "emb": vec_str
            })
            print(f"  [{i+1}/{len(EXAMPLES)}] {ex['natural_language'][:60]}... ✓")
        conn.commit()

    print(f"◈ {len(EXAMPLES)} few-shot examples seeded successfully.")


if __name__ == "__main__":
    main()

"""
Idempotent database setup. Safe to run on every app start.
Creates pgvector extension + all three vector tables + Chinook schema.

Implementation lives in db.schema_init (shared with FastAPI lifespan).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from nixus.db.schema_init import init_database

if __name__ == "__main__":
    init_database()

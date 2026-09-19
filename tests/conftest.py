"""Offline-suite environment defaults.

``tests/`` is the offline unit suite: every LLM call and database touchpoint is
monkeypatched. IMPORTING the application is import-side-effect free for the
OpenAI client (nixus/utils/embeddings.py constructs it lazily on first openai
call), but the SQLAlchemy engines are still built eagerly, which raises on
missing configuration. load_dotenv() runs first so values from a real .env win;
setdefault fills only what is genuinely absent. The fill values are the
.env.example SENTINELS on purpose: nixus.config.is_placeholder recognizes them,
so live-LLM tests (_NEEDS_KEY gates) skip instead of dialing the real API with
a bogus key. No real credentials are ever required by tests/.
"""
import os

from dotenv import load_dotenv

load_dotenv()

os.environ.setdefault("OPENAI_API_KEY", "your_openai_api_key_here")
os.environ.setdefault("ANTHROPIC_API_KEY", "your_anthropic_api_key_here")
os.environ.setdefault(
    "STATE_DATABASE_URL", "postgresql://nixus:nixus@localhost:5433/nixus_sql"
)
os.environ.setdefault(
    "TARGET_DATABASE_URL",
    "postgresql://nixus_readonly:nixus_readonly@localhost:5433/nixus_saas",
)

from dotenv import load_dotenv
from openai import AsyncOpenAI

from nixus.config import settings
from nixus.utils.retry import embedding_retry

load_dotenv()

_async_client = AsyncOpenAI(api_key=settings.openai_api_key)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536


@embedding_retry
async def _create_embedding(text: str) -> list[float]:
    response = await _async_client.embeddings.create(
        input=[text],
        model=EMBEDDING_MODEL,
    )
    return response.data[0].embedding


@embedding_retry
async def _create_embeddings(texts: list[str]) -> list[list[float]]:
    response = await _async_client.embeddings.create(
        input=texts,
        model=EMBEDDING_MODEL,
    )
    return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]


async def embed_text(text: str) -> list[float]:
    """
    Embed a single text string using OpenAI's text-embedding-3-small model.
    Returns a list of 1536 floats. Retries up to 3 times on transient errors;
    re-raises on persistent failure (callers handle final failure).
    """
    text = text.replace("\n", " ").strip()[:20000]
    return await _create_embedding(text)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed multiple texts in a single batched API call.
    Returns embeddings in the same order as the input list.
    """
    texts = [t.replace("\n", " ").strip()[:20000] for t in texts]
    return await _create_embeddings(texts)


# Backwards-compat alias for any caller still using the old sync name.
embed_batch = embed_texts

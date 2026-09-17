"""
Shared retry decorators for LLM and embedding API calls.
Uses tenacity with exponential backoff. Import and apply these
decorators at call sites, not at the function definition level,
so they can be tested without patching decorators.
"""
import logging

from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

try:
    from anthropic import APIConnectionError as AnthropicConnectionError
    from anthropic import APIStatusError as AnthropicStatusError  # noqa: F401
    from anthropic import InternalServerError as AnthropicInternalError
    from anthropic import RateLimitError as AnthropicRateLimitError
    _anthropic_errors = (
        AnthropicRateLimitError,
        AnthropicConnectionError,
        AnthropicInternalError,
    )
except ImportError:
    _anthropic_errors = (Exception,)

try:
    from openai import APIConnectionError as OpenAIConnectionError
    from openai import InternalServerError as OpenAIInternalError
    from openai import RateLimitError as OpenAIRateLimitError
    _openai_errors = (
        OpenAIRateLimitError,
        OpenAIConnectionError,
        OpenAIInternalError,
    )
except ImportError:
    _openai_errors = (Exception,)

_retryable_errors = _anthropic_errors + _openai_errors


def llm_retry(func):
    """
    Retry decorator for async LLM ainvoke calls.
    3 attempts, exponential backoff: 2s → 4s → 8s.
    Logs each retry attempt at WARNING level with the exception.
    """
    return retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception_type(_retryable_errors),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )(func)


def embedding_retry(func):
    """
    Retry decorator for async embedding calls.
    3 attempts, exponential backoff: 1s → 2s → 4s.
    Shorter waits than LLM since embeddings are lighter calls.
    """
    return retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception_type(_retryable_errors),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )(func)

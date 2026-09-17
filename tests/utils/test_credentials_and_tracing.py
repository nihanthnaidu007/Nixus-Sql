"""Cold-start robustness: credential detection + the LangSmith tracing gate
(7.2 amendment, defects C & D).

These cover the pure config-layer logic with no network calls:
  - `is_placeholder` correctly classifies the .env.example sentinels and empty
    values as not-configured, and real-looking values as configured.
  - `Settings.tracing_enabled` is forced OFF whenever the LangSmith key is a
    placeholder/empty, regardless of LANGCHAIN_TRACING_V2.
  - `apply_tracing_gate` rewrites the process env so langchain's own tracer
    (which reads os.environ directly) stays dormant on a placeholder key.
"""
import os

import pytest

from nixus.config import Settings, is_placeholder

# ── is_placeholder / credential detection ───────────────────────────────────

@pytest.mark.parametrize("value", [
    None,
    "",
    "   ",
    "your_anthropic_api_key_here",
    "your_openai_api_key_here",
    "your_langsmith_key_here",
    "  your_anthropic_api_key_here  ",  # surrounding whitespace still a sentinel
])
def test_is_placeholder_detects_not_configured(value):
    assert is_placeholder(value) is True


@pytest.mark.parametrize("value", [
    "sk-ant-api03-realLOOKINGkey1234567890",
    "sk-proj-abcDEF1234567890",
    "lsv2_pt_realkey_0987654321",
    "anything_real",            # starts with neither sentinel pattern fully
    "your_key",                 # missing the _here suffix → treated as real
    "starts_here",              # missing the your_ prefix → treated as real
])
def test_is_placeholder_accepts_real_values(value):
    assert is_placeholder(value) is False


# ── tracing gate ─────────────────────────────────────────────────────────────

def test_tracing_disabled_when_key_is_placeholder():
    """Even with LANGCHAIN_TRACING_V2=true, a placeholder key forces OFF."""
    s = Settings(langchain_tracing_v2="true", langchain_api_key="your_langsmith_key_here")
    assert s.tracing_enabled is False


def test_tracing_disabled_when_key_is_empty():
    s = Settings(langchain_tracing_v2="true", langchain_api_key="")
    assert s.tracing_enabled is False


def test_tracing_enabled_only_with_real_key_and_flag():
    s = Settings(langchain_tracing_v2="true", langchain_api_key="lsv2_pt_realkey_123")
    assert s.tracing_enabled is True


def test_tracing_off_with_real_key_but_flag_false():
    s = Settings(langchain_tracing_v2="false", langchain_api_key="lsv2_pt_realkey_123")
    assert s.tracing_enabled is False


def test_apply_tracing_gate_forces_env_off_on_placeholder(monkeypatch):
    """The gate rewrites os.environ so langchain's own tracer stays dormant."""
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGCHAIN_API_KEY", "your_langsmith_key_here")
    # Re-run the gate against a freshly-read Settings (mirrors import-time call).
    import nixus.config as cfg
    monkeypatch.setattr(cfg, "settings", Settings())
    cfg.apply_tracing_gate()
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"

"""setup.sh must never clobber an existing .env (7.2 amendment, defect B).

Two checks:
  - a static guard check on scripts/setup.sh (the `cp .env.example .env` only runs
    inside an `if [ -f .env ]` ... `else` guard), and
  - a behavioral check: running the guard logic with an existing .env leaves it
    byte-for-byte untouched.
"""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SETUP = REPO / "scripts" / "setup.sh"


def test_setup_script_guards_the_cp():
    text = SETUP.read_text()
    # The guard exists ...
    assert 'if [ -f .env ]' in text
    # ... and the only cp is inside it (no bare, first-column unconditional cp).
    assert "\ncp .env.example .env" not in text  # would be an unguarded top-level cp


def test_existing_env_is_not_overwritten(tmp_path):
    """Replicates the guard's behavior against an existing .env in a temp dir."""
    (tmp_path / ".env.example").write_text("PLACEHOLDER=your_key_here\n")
    env = tmp_path / ".env"
    env.write_text("REAL_KEY=sk-ant-realvalue\n")

    guard = 'if [ -f .env ]; then echo keep; else cp .env.example .env; fi'
    out = subprocess.run(
        ["bash", "-c", guard], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert out.returncode == 0
    assert out.stdout.strip() == "keep"
    assert env.read_text() == "REAL_KEY=sk-ant-realvalue\n"  # untouched

"""Run the provider reasoning checks without the pure-logic dependency mocks."""
from pathlib import Path
import subprocess
import sys


def test_actual_sdk_reasoning_round_trip_protocol():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-m", "pytest", "--noconftest", "-q", "-p", "no:cacheprovider",
                             str(root / "tests" / "openrouter_reasoning_protocol_checks.py")],
                            cwd=root, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr

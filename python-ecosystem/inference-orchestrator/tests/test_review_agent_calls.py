"""Exercise genuine provider/message classes outside the unit suite's mocks."""
from pathlib import Path
import subprocess
import sys


def test_native_review_protocol_with_real_provider_adapters():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([
        sys.executable, "-m", "pytest", "--noconftest", "-q", "-p", "no:cacheprovider",
        str(root / "tests" / "review_agent_protocol_checks.py"),
    ], cwd=root, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


def test_openrouter_response_parsing_with_real_sdk_transport():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([
        sys.executable, "-m", "pytest", "--noconftest", "-q", "-p", "no:cacheprovider",
        str(root / "tests" / "review_openrouter_stream_checks.py"),
    ], cwd=root, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr

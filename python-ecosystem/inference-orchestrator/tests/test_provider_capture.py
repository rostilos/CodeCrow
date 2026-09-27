"""Run capture checks with actual provider SDKs, outside the unit suite mocks."""
from pathlib import Path
import subprocess
import sys


def test_provider_capture_at_real_sdk_transport_boundary():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([
        sys.executable, "-m", "pytest", "--noconftest", "-q", "-p", "no:cacheprovider",
        str(root / "tests" / "provider_capture_protocol_checks.py"),
    ], cwd=root, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr

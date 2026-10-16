"""Keep real SDK behavior covered despite unit-suite dependency stubs."""
import os
from pathlib import Path
import subprocess
import sys


def test_terminal_tool_choice_reaches_provider_payload():
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name('terminal_sdk_boundary.py'))],
        cwd=project, env={**os.environ, 'PYTHONPATH': str(project / 'src'), 'MCP_USE_ANONYMIZED_TELEMETRY': 'false'},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

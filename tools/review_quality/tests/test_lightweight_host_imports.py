import os
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[3]
INFERENCE_SOURCE = (
    PROJECT_ROOT / "python-ecosystem" / "inference-orchestrator" / "src"
)


def test_provider_free_leaf_imports_do_not_initialize_mcp_runtime():
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join((
        str(INFERENCE_SOURCE),
        environment.get("PYTHONPATH", ""),
    ))
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys;"
                "import service.review.orchestrator.context_helpers;"
                "import service.review.orchestrator.verification_agent;"
                "assert 'mcp_use' not in sys.modules"
            ),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_prompt_capture_imports_no_agent_or_review_provider_sdk():
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join((
        str(INFERENCE_SOURCE),
        environment.get("PYTHONPATH", ""),
    ))
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys;"
                "import service.review.prompt_dry_run;"
                "assert 'mcp_use' not in sys.modules;"
                "assert 'langchain_openai' not in sys.modules;"
                "assert 'langchain_anthropic' not in sys.modules;"
                "assert 'langchain_google_genai' not in sys.modules"
            ),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

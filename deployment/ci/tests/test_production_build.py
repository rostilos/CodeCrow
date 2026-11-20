"""Run the local entry point with offline stages, including concurrent failures."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
STAGE = '''import os, pathlib, sys, time
root = pathlib.Path(os.environ["STAGES"])
name = os.environ.get("PYTHON_TEST_GROUP", "ci")
(root / (name + ".started")).touch()
deadline = time.monotonic() + 5
while not all((root / (n + ".started")).exists() for n in ("rag", "inference", "ci")):
    if time.monotonic() > deadline:
        sys.exit(90)
    time.sleep(.01)
if name == "ci":
    assert os.environ["CODECROW_DEPLOY_SERVICES"] == "all"
    assert os.environ["CODECROW_DOCKER_OUTPUT"] == "load"
    assert os.environ["CODECROW_DOCKER_OBSERVABILITY"] == "disabled"
(root / (name + ".finished")).touch()
sys.exit(17 if os.environ.get("FAIL_STAGE") == name else 0)
'''


class ProductionBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ("deployment/build", "deployment/ci", "frontend", "bin", "stages",
                     "python-ecosystem/inference-orchestrator/src", "python-ecosystem/rag-pipeline"):
            (self.root / name).mkdir(parents=True)
        for service in ("inference-orchestrator", "rag-pipeline", "web-frontend"):
            path = self.root / "deployment/config" / service
            path.mkdir(parents=True)
            (path / ".env").write_text("TEST=true\n")
        (self.root / "frontend/package.json").write_text("{}")
        self.script = self.root / "deployment/build/production-build.sh"
        shutil.copy(ROOT / "deployment/build/production-build.sh", self.script)
        for name in ("python-tests.sh", "ci-build.sh"):
            path = self.root / "deployment/ci" / name
            path.write_text(f"#!{sys.executable}\n" + STAGE)
            path.chmod(0o755)
        (self.root / "deployment/ci/prepare-python-test-environment.sh").write_text("exit 0\n")
        for name, body in (("git", "echo fixture-commit"), ("docker", 'echo "$*" >> "$STAGES/docker"')):
            path = self.root / "bin" / name
            path.write_text("#!/bin/bash\n" + body + "\n")
            path.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{self.root / 'bin'}:{os.environ['PATH']}",
                        PYTHON_BOOTSTRAP=sys.executable, STAGES=str(self.root / "stages"),
                        CI_LOG_DIR=str(self.root / "logs"), CODECROW_DEPLOY_SERVICES="frontend")

    def run_build(self, *args, **env):
        return subprocess.run(["bash", str(self.script), *args], env=dict(self.env, **env),
                              capture_output=True, text=True, timeout=15)

    def assert_joined(self):
        for name in ("rag", "inference", "ci"):
            self.assertTrue((self.root / "stages" / (name + ".finished")).exists(), name)

    def test_build_only_runs_all_stages_concurrently_without_deployment(self):
        result = self.run_build("--build-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_joined()
        self.assertFalse((self.root / "stages/docker").exists())
        for group in ("rag", "inference"):
            self.assertTrue((self.root / "logs" / f"python-{group}.log").exists())

    def test_each_failure_joins_other_stages_and_prevents_deployment(self):
        for name in ("rag", "inference", "ci"):
            with self.subTest(stage=name):
                for marker in (self.root / "stages").iterdir():
                    marker.unlink()
                result = self.run_build(FAIL_STAGE=name)
                self.assertEqual(result.returncode, 17, result.stdout + result.stderr)
                self.assert_joined()
                self.assertFalse((self.root / "stages/docker").exists())

    def test_success_deploys_only_after_every_stage_passes(self):
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_joined()
        self.assertIn("up -d --no-build --wait", (self.root / "stages/docker").read_text())


if __name__ == "__main__":
    unittest.main()

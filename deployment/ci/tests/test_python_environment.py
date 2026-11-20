"""Exercise environment reuse and invalidation without network access."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]


class PythonEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        scripts = self.root / "deployment/ci"
        scripts.mkdir(parents=True)
        self.prepare = scripts / "prepare-python-test-environment.sh"
        shutil.copy(ROOT / "deployment/ci" / self.prepare.name, self.prepare)
        self.installer = scripts / "install-python-test-dependencies.sh"
        self.installer.write_text('#!/bin/bash\necho install >> "$CALLS"\nexit "${INSTALL_STATUS:-0}"\n')
        self.installer.chmod(0o755)
        self.requirements = self.root / "python-ecosystem/rag-pipeline"
        self.requirements.mkdir(parents=True)
        (self.requirements / "requirements.txt").write_text('-c requirements.lock\npytest\n')
        (self.requirements / "requirements.lock").write_text('pytest==8.4.2\n')
        self.bootstrap = self.root / "bootstrap"
        self.bootstrap.write_text('''#!/bin/bash
set -e
if [ "$1" = -c ]; then
    echo "${RUNTIME:-python-3.11}"
else
    echo create >> "$CALLS"
    mkdir -p "$4/bin"
    printf '#!/bin/bash\nexit "${CHECK_STATUS:-0}"\n' > "$4/bin/python"
    chmod +x "$4/bin/python"
fi
''')
        self.bootstrap.chmod(0o755)
        self.venv = self.root / "venv"
        self.calls = self.root / "calls"
        self.env = dict(os.environ, CALLS=str(self.calls))

    def prepare_environment(self, **env):
        return subprocess.run(
            ["bash", str(self.prepare), "rag", str(self.venv), str(self.bootstrap)],
            env=dict(self.env, **env), capture_output=True, text=True,
        )

    def assert_ready(self, **env):
        result = self.prepare_environment(**env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_reuses_unchanged_environment_but_invalidates_changed_inputs(self):
        self.assert_ready()
        self.assert_ready()
        self.assertEqual(self.calls.read_text().splitlines(), ["create", "install"])
        for path in (self.requirements / "requirements.txt", self.requirements / "requirements.lock", self.installer):
            path.write_text(path.read_text() + "\n# changed\n")
            self.assert_ready()
        self.assert_ready(RUNTIME="python-3.11-new")
        self.assertEqual(self.calls.read_text().splitlines().count("create"), 5)

    def test_failed_install_is_not_cached(self):
        result = self.prepare_environment(INSTALL_STATUS="7")
        self.assertEqual(result.returncode, 7)
        self.assertFalse((self.venv / ".requirements.sha256").exists())
        self.assert_ready()
        self.assertEqual(self.calls.read_text().splitlines().count("install"), 2)

    def test_broken_environment_is_recreated(self):
        self.assert_ready()
        self.assert_ready(CHECK_STATUS="1")
        self.assertEqual(self.calls.read_text().splitlines().count("create"), 2)


if __name__ == "__main__":
    unittest.main()

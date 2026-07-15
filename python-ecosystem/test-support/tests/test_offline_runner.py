from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
RUNNER = REPOSITORY_ROOT / "tools" / "offline-harness" / "bin" / "run-offline.sh"


def test_runner_fails_closed_without_command_or_bubblewrap() -> None:
    no_command = subprocess.run([str(RUNNER)], text=True, capture_output=True, check=False)
    assert no_command.returncode == 64
    assert "usage:" in no_command.stderr

    environment = os.environ.copy()
    environment["CODECROW_BWRAP_BIN"] = "/definitely/missing/bwrap"
    missing = subprocess.run(
        [str(RUNNER), "/usr/bin/true"],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert missing.returncode == 69
    assert "refusing an override" in missing.stderr

    artifact_root = REPOSITORY_ROOT / ".llm-handoff-artifacts" / "p0-03"
    artifact_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=artifact_root) as directory:
        marker = Path(directory) / "override-command-ran"
        override_environment = os.environ.copy()
        override_environment["CODECROW_BWRAP_BIN"] = "/usr/bin/true"
        override = subprocess.run(
            [str(RUNNER), "/usr/bin/touch", str(marker)],
            env=override_environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert override.returncode == 69
        assert "refusing an override" in override.stderr
        assert not marker.exists()

    outside = subprocess.run(
        [str(RUNNER), "/usr/bin/true"],
        cwd="/tmp",
        text=True,
        capture_output=True,
        check=False,
    )
    assert outside.returncode == 65
    assert "working directory" in outside.stderr

    escaped_ledger_environment = os.environ.copy()
    escaped_ledger_environment["CODECROW_EXTERNAL_CALL_LEDGER"] = "/tmp/escaped.json"
    escaped_ledger = subprocess.run(
        [str(RUNNER), "/usr/bin/true"],
        env=escaped_ledger_environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert escaped_ledger.returncode == 65
    assert "ledger path" in escaped_ledger.stderr

    escaped_directory_environment = os.environ.copy()
    escaped_directory_environment["CODECROW_EXTERNAL_CALL_LEDGER_DIR"] = "/tmp/escaped"
    escaped_directory = subprocess.run(
        [str(RUNNER), "/usr/bin/true"],
        env=escaped_directory_environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert escaped_directory.returncode == 65
    assert "ledger directory" in escaped_directory.stderr

    unapproved_cache_environment = os.environ.copy()
    unapproved_cache_environment["CODECROW_MAVEN_REPOSITORY"] = "/tmp"
    unapproved_cache = subprocess.run(
        [str(RUNNER), "/usr/bin/true"],
        env=unapproved_cache_environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert unapproved_cache.returncode == 65
    assert "Maven repository" in unapproved_cache.stderr

    with tempfile.TemporaryDirectory(dir=artifact_root) as directory:
        credential_link = Path(directory) / ".env.symlink-smoke"
        credential_link.symlink_to("/dev/null")
        symlink_result = subprocess.run(
            [str(RUNNER), "/usr/bin/true"],
            text=True,
            capture_output=True,
            check=False,
        )
    assert symlink_result.returncode == 65
    assert "credential symlink" in symlink_result.stderr


def test_ci_uses_one_attested_workspace_cache_and_bounded_offline_profiles() -> None:
    workflow = (REPOSITORY_ROOT / ".github/workflows/offline-tests.yml").read_text()

    assert "MAVEN_OPTS:" not in workflow
    assert "runs-on: ubuntu-24.04" in workflow
    assert "timeout-minutes: 90" in workflow
    assert "cache: maven" not in workflow
    assert "-DskipTests dependency:go-offline" in workflow
    assert "-Dmaven.repo.local=\"$GITHUB_WORKSPACE/$MAVEN_REPOSITORY\"" in workflow
    assert "tools/offline-harness/maven/settings-ci.xml" in workflow
    assert 'export CODECROW_MAVEN_REPOSITORY="$GITHUB_WORKSPACE/$MAVEN_REPOSITORY"' in workflow
    assert "cache-dependency-path: tools/offline-harness/requirements/ci-test.lock" in workflow
    assert "PYTHON_ENV: .llm-handoff-artifacts/p0-03/locked-python311" in workflow
    assert "pip --isolated install --require-hashes" in workflow
    assert "--index-url https://pypi.org/simple/" in workflow
    assert "--report \"$GITHUB_WORKSPACE/.llm-handoff-artifacts" in workflow
    assert "manifest-maven-cache.py" in workflow
    assert "validate-build-provenance.py" in workflow
    assert "validate-persistence-images.py" in workflow
    assert "--print-runtime-references" in workflow
    assert "/usr/bin/docker pull --platform linux/amd64" in workflow
    assert "DOCKER_CONFIG=\"$DOCKER_CONFIG_ROOT\"" in workflow
    assert "docker push" not in workflow
    assert "python-lock-sha256.txt" in workflow
    assert "pip install pytest" not in workflow
    assert ".venv/bin" not in workflow
    assert "-pl libs/test-support -am clean verify" in workflow
    assert "tests/p003_production_adapter_contracts" in workflow
    assert "python-production-adapters.json" in workflow
    for selector in (
        "VcsConnectionAdapterContractTest",
        "JiraCloudAdapterContractTest",
        "EmailSmtpAdapterContractTest",
    ):
        assert f"-Dtest={selector}" in workflow


def test_runner_unshares_network_and_clears_credentials() -> None:
    masked_env_files = sorted(
        path
        for path in REPOSITORY_ROOT.rglob(".env*")
        if (path.name == ".env" or path.name.startswith(".env."))
        and ".venv" not in path.parts
        and "node_modules" not in path.parts
    )
    assert masked_env_files, "repository safety smoke requires an existing .env file"
    program = """
import os
import socket
import subprocess
import sys
from pathlib import Path

assert os.environ.get('OPENAI_API_KEY') is None
assert os.environ.get('GITHUB_TOKEN') is None
assert os.environ.get('AWS_ACCESS_KEY_ID') is None
assert os.environ.get('LANGSMITH_API_KEY') is None
assert os.environ.get('UNLISTED_HOST_SECRET') is None
assert os.environ.get('DOCKER_HOST') is None
assert os.environ.get('SSH_AUTH_SOCK') is None
assert os.environ['HOME'] == '/tmp/codecrow-home'
assert 'SERVICE_SECRET' not in os.environ
assert os.environ['CODECROW_INTERNAL_SECRET'] == 'test-secret-token'
assert os.environ['TESTCONTAINERS_RYUK_DISABLED'] == 'true'
assert os.environ['TESTCONTAINERS_REUSE_ENABLE'] == 'false'
assert os.environ['PYTHONHASHSEED'] == '0'
assert os.environ['CODECROW_EXTERNAL_CALL_LEDGER_DIR'].startswith(
    str(Path.cwd() / '.llm-handoff-artifacts' / 'p0-03')
)
assert Path(os.environ['CODECROW_EXTERNAL_CALL_LEDGER_DIR']).is_dir()
assert not Path('/run/docker.sock').exists()
assert not Path('/var/run/docker.sock').exists()
process_roots = list(Path('/proc').glob('[0-9]*/root'))
assert len(process_roots) < 10
assert all(not (root / 'run/docker.sock').exists() for root in process_roots)
try:
    masked_content = Path(sys.argv[1]).read_bytes()
except PermissionError:
    masked_content = b''
assert masked_content == b''
assert not Path(sys.argv[2]).exists()
java = subprocess.run(
    [str(Path(os.environ['JAVA_HOME']) / 'bin' / 'java'), '-version'],
    text=True,
    capture_output=True,
    check=False,
)
assert java.returncode == 0
assert f'version "{sys.argv[3]}.' in java.stderr
try:
    socket.getaddrinfo('provider.invalid', 443)
except socket.gaierror:
    print('external-dns-blocked')
else:
    raise AssertionError('network namespace allowed external DNS')
try:
    socket.create_connection(('192.0.2.10', 443), timeout=0.05)
except OSError:
    print('external-network-blocked')
else:
    raise AssertionError('network namespace allowed an external socket')
"""
    environment = os.environ.copy()
    environment["OPENAI_API_KEY"] = "must-not-enter-namespace"
    environment["GITHUB_TOKEN"] = "must-not-enter-namespace"
    environment["AWS_ACCESS_KEY_ID"] = "must-not-enter-namespace"
    environment["LANGSMITH_API_KEY"] = "must-not-enter-namespace"
    environment["UNLISTED_HOST_SECRET"] = "must-not-enter-namespace"
    host_java = subprocess.run(
        ["/usr/bin/java", "-version"], text=True, capture_output=True, check=True
    )
    host_java_major = re.search(r'version "(\d+)', host_java.stderr)
    assert host_java_major is not None
    with tempfile.TemporaryDirectory(
        prefix="p003-hidden-workspace-sibling-", dir=REPOSITORY_ROOT.parent
    ) as sibling_directory:
        sibling_sentinel = Path(sibling_directory) / "must-stay-hidden"
        sibling_sentinel.write_text("unrelated-workspace-data")
        result = subprocess.run(
            [
                str(RUNNER),
                os.sys.executable,
                "-c",
                program,
                str(masked_env_files[0]),
                str(sibling_sentinel),
                host_java_major.group(1),
            ],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["external-dns-blocked", "external-network-blocked"]


def test_runner_preserves_a_checkout_nested_under_home() -> None:
    with tempfile.TemporaryDirectory(
        prefix="p003-hidden-home-sibling-", dir=Path.home()
    ) as sibling_directory, tempfile.TemporaryDirectory(
        prefix="p003-home-workspace-", dir=Path.home()
    ) as directory:
        hidden_sentinel = Path(sibling_directory) / "must-stay-hidden"
        hidden_sentinel.write_text("host-home-data")
        mirrored_root = Path(directory)
        mirrored_bin = mirrored_root / "tools" / "offline-harness" / "bin"
        mirrored_bin.mkdir(parents=True)
        mirrored_runner = mirrored_bin / "run-offline.sh"
        mirrored_runner.symlink_to(RUNNER)
        program = """
from pathlib import Path
import sys
assert Path(sys.argv[1]).is_dir()
assert not Path(sys.argv[2]).exists()
"""
        result = subprocess.run(
            [
                str(mirrored_runner),
                "/usr/bin/python3",
                "-c",
                program,
                str(mirrored_root),
                str(hidden_sentinel),
            ],
            cwd=mirrored_root,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr


def test_runner_entry_and_runtime_probes_ignore_host_startup_injection() -> None:
    artifact_root = REPOSITORY_ROOT / ".llm-handoff-artifacts" / "p0-03"
    with tempfile.TemporaryDirectory(dir=artifact_root) as directory:
        temporary = Path(directory)
        bash_marker = temporary / "bash-env-executed"
        helper_marker = temporary / "hostile-path-helper-executed"
        python_marker = temporary / "sitecustomize-executed"
        java_marker = temporary / "java-options-executed.jfr"

        bash_env = temporary / "bash-env.sh"
        bash_env.write_text(f"/usr/bin/touch {bash_marker}\n", encoding="utf-8")
        hostile_bin = temporary / "hostile-bin"
        hostile_bin.mkdir()
        for helper in ("realpath", "dirname", "getent", "id", "cut", "sed", "head", "find"):
            shim = hostile_bin / helper
            shim.write_text(
                f"#!/bin/sh\n/usr/bin/touch {helper_marker}\nexit 91\n",
                encoding="utf-8",
            )
            shim.chmod(0o755)
        hostile_python = temporary / "python-startup"
        hostile_python.mkdir()
        (hostile_python / "sitecustomize.py").write_text(
            f"from pathlib import Path\nPath({str(python_marker)!r}).touch()\n",
            encoding="utf-8",
        )

        java_environment = os.environ.copy()
        java_environment["JAVA_TOOL_OPTIONS"] = (
            f"-XX:StartFlightRecording=filename={java_marker},dumponexit=true"
        )
        payload = subprocess.run(
            ["/usr/bin/java", "-version"],
            env=java_environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert payload.returncode == 0
        assert java_marker.is_file(), "the Java option marker must be a potent payload"
        java_marker.unlink()

        environment = os.environ.copy()
        environment["BASH_ENV"] = str(bash_env)
        environment["ENV"] = str(bash_env)
        environment["PATH"] = str(hostile_bin)
        environment["PYTHONPATH"] = str(hostile_python)
        environment["PYTHONHOME"] = str(temporary / "invalid-python-home")
        environment["JAVA_TOOL_OPTIONS"] = java_environment["JAVA_TOOL_OPTIONS"]
        environment["_JAVA_OPTIONS"] = "-Dcodecrow.hostile=true"
        environment["JDK_JAVA_OPTIONS"] = "-Dcodecrow.hostile=true"
        result = subprocess.run(
            [str(RUNNER), sys.executable, "-I", "-S", "-c", "print('isolated')"],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "isolated\n"
        assert not bash_marker.exists()
        assert not helper_marker.exists()
        assert not python_marker.exists()
        assert not java_marker.exists()


def test_runner_source_accepts_setup_python_layout_and_uses_isolated_probes() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert source.startswith(
        "#!/bin/bash -p\nPATH=/usr/sbin:/usr/bin:/sbin:/bin\nexport PATH\n"
    )
    assert "/opt/hostedtoolcache/Python/3.11.*/x64/bin/python*" in source
    assert "/opt/hostedtoolcache/Python/3.11.*/arm64/bin/python*" in source
    assert source.count("/usr/bin/env -i") >= 3
    assert "\"$COMMAND_REALPATH\" -I -S -c" in source
    assert "--setenv CODECROW_EXTERNAL_CALL_LEDGER_DIR" in source
    assert "--setenv PYTHONHASHSEED 0" in source
    assert "certifi-cacert.sha256" in source


def test_locked_python_certifi_bundle_is_the_only_integrity_checked_pem_exception() -> None:
    locked_python = (
        REPOSITORY_ROOT
        / ".llm-handoff-artifacts"
        / "p0-03"
        / "locked-python311"
        / "bin"
        / "python"
    )
    if not locked_python.exists():
        return
    program = """
import certifi
import ssl
from pathlib import Path
bundle = Path(certifi.where())
assert bundle.name == 'cacert.pem'
assert b'# Issuer:' in bundle.read_bytes()[:100]
ssl.create_default_context(cafile=str(bundle))
print('certifi-ok')
"""
    result = subprocess.run(
        [str(RUNNER), str(locked_python), "-I", "-c", program],
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "certifi-ok\n"


def test_runner_rejects_artifact_directory_symlink_before_writing() -> None:
    with tempfile.TemporaryDirectory(
        prefix="p003-artifact-link-repo-", dir=REPOSITORY_ROOT.parent
    ) as repository, tempfile.TemporaryDirectory(
        prefix="p003-artifact-link-target-", dir=REPOSITORY_ROOT.parent
    ) as target:
        mirrored_root = Path(repository)
        mirrored_bin = mirrored_root / "tools" / "offline-harness" / "bin"
        mirrored_bin.mkdir(parents=True)
        (mirrored_bin / "run-offline.sh").symlink_to(RUNNER)
        (mirrored_root / ".llm-handoff-artifacts").symlink_to(target)
        result = subprocess.run(
            [str(mirrored_bin / "run-offline.sh"), "/usr/bin/true"],
            cwd=mirrored_root,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 65
        assert "real directories, not links" in result.stderr
        assert list(Path(target).iterdir()) == []


def test_runner_masks_credential_shaped_files_inside_dot_venv() -> None:
    directory = REPOSITORY_ROOT / ".venv" / "p003-hostile-credential"
    directory.mkdir(parents=True, exist_ok=True)
    secret = directory / "leak.key"
    secret.write_text("must-not-enter-the-namespace", encoding="utf-8")
    try:
        result = subprocess.run(
            [
                str(RUNNER),
                "/usr/bin/python3",
                "-I",
                "-S",
                "-c",
                    "from pathlib import Path; import sys; "
                    "\ntry: data = Path(sys.argv[1]).read_bytes()"
                    "\nexcept PermissionError: data = b''"
                    "\nassert data == b''",
                str(secret),
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
    finally:
        secret.unlink(missing_ok=True)
        directory.rmdir()

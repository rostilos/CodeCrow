"""Offline deployment checks: Compose release selection and shell orchestration failures.

These exercise the real scripts with a recording Docker stub. They do not deploy
containers, run database migrations, or verify a remote staging environment.
"""
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SERVICES = ("web-server", "pipeline-agent", "inference-orchestrator", "rag-pipeline", "web-frontend")
ENV = "POSTGRES_PASSWORD=test-password\nINTERNAL_API_SECRET=test-secret\nPGADMIN_DEFAULT_PASSWORD=test-admin\n"
DOCKER_STUB = """import json, os, sys
args = sys.argv[1:]
with open(os.environ['DOCKER_CALLS'], 'a') as log:
    log.write(json.dumps({'args': args, 'docker_config': os.environ.get('DOCKER_CONFIG')}) + '\\n')
if args[0] == 'login':
    sys.stdin.read()
elif 'ps' in args and '--status' in args:
    if os.environ.get('MOCK_POSTGRES_RUNNING') == 'true':
        print('stage-postgres-id')
elif 'exec' in args:
    if os.environ.get('MOCK_BACKUP_FAIL') == 'true':
        sys.exit(1)
    print('-- staged database backup')
elif 'pull' in args and os.environ.get('MOCK_PULL_FAIL') == 'true':
    sys.exit(1)
elif 'up' in args and os.environ.get('MOCK_UP_FAIL') == 'true':
    sys.exit(1)
"""


class StageTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="codecrow-stage-tests-")
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.deploy = self.work / "stage"
        self.deploy.mkdir()
        self.bin = self.work / "bin"
        self.bin.mkdir()
        self.calls_file = self.work / "docker-calls.jsonl"
        docker = self.bin / "docker"
        docker.write_text(f"#!{sys.executable}\n" + DOCKER_STUB)
        docker.chmod(0o755)
        for name in ("server-deploy-stage.sh", "service-selection.sh"):
            shutil.copy(ROOT / "deployment/ci" / name, self.deploy / name)
        shutil.copy(ROOT / "deployment/docker-compose.stage.yml", self.deploy)
        (self.deploy / ".env").write_text(ENV)
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", DOCKER_CALLS=str(self.calls_file), GITHUB_REPOSITORY_OWNER="ExampleOwner")
        for key in ("CODECROW_IMAGE_TAG", "CODECROW_GHCR_LOGIN_STDIN", "CODECROW_DEPLOY_SERVICES"):
            self.env.pop(key, None)

    def run_deploy(self, services="all", tag="stage-test-release", **flags):
        return subprocess.run(
            ["bash", str(self.deploy / "server-deploy-stage.sh")],
            env=dict(self.env, CODECROW_DEPLOY_SERVICES=services, CODECROW_IMAGE_TAG=tag, **flags),
            capture_output=True, text=True,
        )

    def calls(self):
        return [json.loads(line) for line in self.calls_file.read_text().splitlines()] if self.calls_file.exists() else []

    def tags(self):
        return dict(line.split("=", 1) for line in (self.deploy / ".images.env").read_text().splitlines())

    def seed_tags(self):
        (self.deploy / ".images.env").write_text("".join(f"{service.upper().replace('-', '_')}_IMAGE_TAG=stage-old\n" for service in SERVICES))


class StageDeploymentTests(StageTestCase):
    def test_full_deploy_pins_all_services_without_stopping_stack(self):
        result = self.run_deploy(MOCK_POSTGRES_RUNNING="true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(self.tags().values()), {"stage-test-release"})
        self.assertEqual(len(self.tags()), 5)
        calls = [call["args"] for call in self.calls()]
        self.assertTrue(any("pg_dump" in " ".join(call) for call in calls))
        self.assertEqual(len(list((self.deploy / "backups").glob("*.sql.gz"))), 1)
        self.assertTrue(any("--include-deps" in call for call in calls))
        self.assertTrue(any("--wait" in call and "--force-recreate" in call for call in calls))
        self.assertFalse(any(token in call for call in calls for token in ("down", "prune", "--volumes")))
        self.assertTrue(all(call[call.index("--project-name") + 1] == "codecrow-stage" for call in calls))

    def test_partial_frontend_deploy_preserves_backend_revisions(self):
        self.seed_tags()
        result = self.run_deploy("frontend")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.tags()["WEB_FRONTEND_IMAGE_TAG"], "stage-test-release")
        self.assertEqual(self.tags()["RAG_PIPELINE_IMAGE_TAG"], "stage-old")
        self.assertEqual(self.tags()["WEB_SERVER_IMAGE_TAG"], "stage-old")
        up = next(call["args"] for call in self.calls() if "up" in call["args"])
        self.assertEqual(up[-1], "web-frontend")
        self.assertFalse(any("exec" in call["args"] for call in self.calls()))

    def test_redeploy_reuses_last_successful_tags(self):
        self.seed_tags()
        result = self.run_deploy("python", tag="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(self.tags().values()), {"stage-old"})

    def test_redeploy_without_existing_release_reports_error(self):
        result = self.run_deploy(tag="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No deployed staging release exists", result.stderr)
        self.assertFalse(any("up" in call["args"] for call in self.calls()))

    def test_pull_failure_keeps_previous_release_and_does_not_recreate(self):
        self.seed_tags()
        result = self.run_deploy(MOCK_PULL_FAIL="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(set(self.tags().values()), {"stage-old"})
        self.assertFalse(any("up" in call["args"] for call in self.calls()))
        self.assertEqual(list(self.deploy.glob(".deploy-*")), [])

    def test_health_failure_keeps_previous_release_and_database_backup(self):
        self.seed_tags()
        result = self.run_deploy(MOCK_UP_FAIL="true", MOCK_POSTGRES_RUNNING="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(set(self.tags().values()), {"stage-old"})
        self.assertIn("partially updated stack", result.stdout)
        self.assertEqual(len(list((self.deploy / "backups").glob("*.sql.gz"))), 1)
        self.assertEqual(list(self.deploy.glob(".deploy-*")), [])

    def test_failed_database_backup_aborts_before_pull(self):
        result = self.run_deploy(MOCK_POSTGRES_RUNNING="true", MOCK_BACKUP_FAIL="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("pull" in call["args"] for call in self.calls()))
        self.assertEqual(list((self.deploy / "backups").glob("*.sql.gz")), [])

    def test_registry_credentials_are_temporary_and_never_in_argv(self):
        result = subprocess.run(
            ["bash", str(self.deploy / "server-deploy-stage.sh")],
            env=dict(self.env, CODECROW_IMAGE_TAG="stage-test", CODECROW_GHCR_LOGIN_STDIN="true", CODECROW_GHCR_USER="test-user"),
            input="private-test-token\n", capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("private-test-token", self.calls_file.read_text() + result.stdout + result.stderr)
        self.assertTrue(all(call["docker_config"] for call in self.calls()))
        self.assertFalse(Path(self.calls()[0]["docker_config"]).exists())

    def test_backups_are_pruned_only_in_stage_directory(self):
        backups = self.deploy / "backups"
        backups.mkdir()
        for index in range(12):
            (backups / f"codecrow_stage_pre_deploy_20200101_{index:06}.sql.gz").touch()
        (backups / "unrelated.sql.gz").touch()
        result = self.run_deploy("frontend")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list(backups.glob("codecrow_stage_pre_deploy_*.sql.gz"))), 10)
        self.assertTrue((backups / "unrelated.sql.gz").exists())

    def test_custom_image_tag_cannot_inject_another_env_setting(self):
        result = self.run_deploy(tag="stage-ok\nWEB_SERVER_IMAGE_TAG=latest")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.deploy / ".images.env").exists())
        self.assertEqual(self.calls(), [])

    @unittest.skipUnless(shutil.which("docker"), "Docker Compose CLI is required for config rendering")
    def test_rendered_compose_uses_stage_releases_and_supports_sqlite(self):
        env_file = self.work / "render.env"
        env_file.write_text(ENV)
        # Match server-deploy-stage.sh: the CLI sets the project name. Stage
        # runs on a separate VPS and can use production container/volume names
        # and ports; image packages and per-service release tags remain separate.
        def render(image_env=None):
            command = [shutil.which("docker"), "compose", "--project-name", "codecrow-stage", "--env-file", str(env_file)]
            if image_env is not None:
                command.extend(["--env-file", str(image_env)])
            command.extend(["-f", str(ROOT / "deployment/docker-compose.stage.yml"), "config", "--format", "json"])
            env = dict(os.environ, GITHUB_REPOSITORY_OWNER="example")
            for service in SERVICES:
                env.pop(f"{service.upper().replace('-', '_')}_IMAGE_TAG", None)
            result = subprocess.run(
                command, env=env, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        stage = render()
        self.assertEqual(stage["name"], "codecrow-stage")
        for service, definition in stage["services"].items():
            for port in definition.get("ports", []):
                self.assertEqual(port["host_ip"], "127.0.0.1")
            if service in SERVICES:
                self.assertEqual(definition["image"], f"ghcr.io/example/codecrow-stage-{service}:stage")
        self.assertNotIn("qdrant", stage["services"])
        rag = stage["services"]["rag-pipeline"]
        self.assertEqual(rag["environment"]["STRUCTURAL_INDEX_ROOT"], "/var/lib/codecrow/structural-index")
        self.assertTrue(any(v.get("source") == "structural_index_data" for v in rag["volumes"]))
        self.assertEqual(rag["depends_on"]["fix-permissions"]["condition"], "service_completed_successfully")
        self.assertEqual(stage["services"]["web-server"]["environment"]["SPRING_FLYWAY_ENABLED"], "true")
        self.assertEqual(stage["services"]["pipeline-agent"]["environment"]["SPRING_FLYWAY_ENABLED"], "false")
        self.assertEqual(stage["services"]["inference-orchestrator"]["depends_on"]["web-server"]["condition"], "service_healthy")

        # Render the actual release file produced by a partial deployment so
        # literal :latest or :stage references cannot silently ignore its tags.
        self.seed_tags()
        result = self.run_deploy("frontend")
        self.assertEqual(result.returncode, 0, result.stderr)
        pinned = render(self.deploy / ".images.env")
        for service in SERVICES:
            tag = "stage-test-release" if service == "web-frontend" else "stage-old"
            self.assertEqual(pinned["services"][service]["image"], f"ghcr.io/example/codecrow-stage-{service}:{tag}")


class BuildTagTests(StageTestCase):
    def test_build_uses_staging_packages_tags_and_cache(self):
        self.assert_build_tags(stage=True)

    def test_build_preserves_production_defaults(self):
        self.assert_build_tags(stage=False)

    def test_stage_workflow_builds_rag_with_new_relic(self):
        self.assert_build_tags(stage=True, service="rag-pipeline")

    def test_production_builds_rag_with_new_relic(self):
        self.assert_build_tags(stage=False, service="rag-pipeline")

    def assert_build_tags(self, stage, service="web-frontend"):
        repo = self.work / "repo"
        scripts = repo / "deployment/ci"
        scripts.mkdir(parents=True)
        (repo / "frontend").mkdir()
        (repo / "tools").mkdir()
        (repo / "tools/validate_plugin_boundaries.py").write_text("pass\n")
        for name in ("ci-build.sh", "service-selection.sh"):
            shutil.copy(ROOT / "deployment/ci" / name, scripts)
        env = dict(self.env, CODECROW_DEPLOY_SERVICES=service)
        for key in ("CODECROW_REGISTRY_IMAGE_PREFIX", "CODECROW_IMAGE_TAG", "CODECROW_IMAGE_ALIAS", "CODECROW_BUILD_CACHE_PREFIX", "CODECROW_DOCKER_OUTPUT", "CODECROW_DOCKER_OBSERVABILITY"):
            env.pop(key, None)
        if stage:
            env.update(CODECROW_REGISTRY_IMAGE_PREFIX="codecrow-stage", CODECROW_IMAGE_TAG="stage-exact-sha", CODECROW_IMAGE_ALIAS="stage", CODECROW_BUILD_CACHE_PREFIX="stage-")
            # Exercise the setting actually passed by CI, not a hard-coded
            # enabled value that would miss a workflow selecting bare images.
            workflow = (ROOT / ".github/workflows/deploy-stage.yml").read_text()
            observability = re.search(r"^\s+CODECROW_DOCKER_OBSERVABILITY:\s*(\w+)\s*$", workflow, re.MULTILINE)
            if observability:
                env["CODECROW_DOCKER_OBSERVABILITY"] = observability.group(1)
        result = subprocess.run(["bash", str(scripts / "ci-build.sh")], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.calls()[0]["args"]
        if stage:
            self.assertIn(f"ghcr.io/exampleowner/codecrow-stage-{service}:stage-exact-sha", args)
            self.assertIn(f"ghcr.io/exampleowner/codecrow-stage-{service}:stage", args)
            self.assertIn(f"type=gha,scope=stage-codecrow-{service}", args)
            self.assertNotIn(f"ghcr.io/exampleowner/codecrow-{service}:latest", args)
        else:
            self.assertIn(f"ghcr.io/exampleowner/codecrow-{service}:latest", args)
            self.assertIn(f"type=gha,scope=codecrow-{service}", args)
        if service == "rag-pipeline":
            self.assertEqual(args[args.index("-f") + 1], "python-ecosystem/rag-pipeline/Dockerfile.observable")
        self.assertIn("--push", args)


class RunnerUploadTests(StageTestCase):
    def setUp(self):
        super().setUp()
        ssh = self.bin / "ssh"
        ssh.write_text(f"#!{sys.executable}\n" + """import os, subprocess, sys
sys.exit(subprocess.run(sys.argv[-1], shell=True, executable='/bin/bash', input=sys.stdin.buffer.read(), env=os.environ).returncode)
""")
        ssh.chmod(0o755)
        self.runner_env = dict(
            self.env, STAGE_DEPLOY_SSH_KEY="test-key", STAGE_DEPLOY_HOST="stage.example.com",
            STAGE_DEPLOY_USER="stage-user", STAGE_DEPLOY_HOST_FINGERPRINT="stage.example.com ssh-ed25519 test-host-key",
            STAGE_ENV_DEPLOYMENT=ENV, STAGE_ENV_JAVA_SHARED="codecrow.security.jwtSecret=stage-jwt",
            STAGE_ENV_INFERENCE_ORCHESTRATOR="MAX_CONCURRENT_REVIEWS=16",
            STAGE_ENV_RAG_PIPELINE="RAG_FULL_INDEX_CONCURRENCY=16",
            STAGE_GITHUB_APP_PRIVATE_KEY="private-stage-test-key", GHCR_TOKEN="private-test-registry-token",
            GHCR_USER="test-user", CODECROW_IMAGE_TAG="stage-upload", STAGE_DEPLOY_PATH=str(self.deploy),
        )

    def run_runner(self, **overrides):
        return subprocess.run(["bash", str(ROOT / "deployment/ci/deploy-stage.sh")], env=dict(self.runner_env, **overrides), capture_output=True, text=True)

    def test_upload_keeps_config_private_and_handles_shell_characters_in_path(self):
        destination = self.work / "stage space; touch unintended-file"
        result = self.run_runner(STAGE_DEPLOY_PATH=str(destination))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((ROOT / "unintended-file").exists())
        for file in (".env", "config/java-shared/application.properties", "config/rag-pipeline/.env", "config/java-shared/github-private-key/github-app-private-key.pem"):
            self.assertEqual((destination / file).stat().st_mode & 0o777, 0o600)
        self.assertNotIn("private-test-registry-token", result.stdout + result.stderr + self.calls_file.read_text())
        self.assertEqual(list(destination.glob(".deploy-*")), [])

    def test_partial_upload_preserves_unselected_service_config(self):
        config = self.deploy / "config/rag-pipeline/.env"
        config.parent.mkdir(parents=True)
        config.write_text("existing-stage-rag-config")
        result = self.run_runner(CODECROW_DEPLOY_SERVICES="frontend", STAGE_ENV_JAVA_SHARED="", STAGE_ENV_INFERENCE_ORCHESTRATOR="", STAGE_ENV_RAG_PIPELINE="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(config.read_text(), "existing-stage-rag-config")

    def test_missing_stage_secret_does_not_fall_back_to_production(self):
        result = self.run_runner(STAGE_DEPLOY_SSH_KEY="", DEPLOY_SSH_KEY="live-key")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("STAGE_DEPLOY_SSH_KEY is required", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_production_directory_cannot_be_selected(self):
        result = self.run_runner(STAGE_DEPLOY_PATH="/opt/codecrow")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("separate from /opt/codecrow", result.stderr)
        self.assertEqual(self.calls(), [])


class InitializationTests(StageTestCase):
    def test_initialization_preserves_existing_stage_credentials(self):
        destination = self.work / "initialized-stage"
        command = ["bash", str(ROOT / "deployment/ci/server-init-stage.sh"), pwd.getpwuid(os.getuid()).pw_name, str(destination)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((destination / "config/java-shared/github-private-key").is_dir())
        (destination / ".env").write_text("existing-stage-credentials")
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((destination / ".env").read_text(), "existing-stage-credentials")
        self.assertEqual(destination.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()

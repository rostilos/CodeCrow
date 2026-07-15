from __future__ import annotations

import json
import re
import runpy
import sys
import xml.etree.ElementTree as ET
from argparse import Namespace
from pathlib import Path

import pytest

from quality_gates import java_legacy_it as legacy

from quality_gates.java_legacy_it import (
    EvidenceError,
    LANE_CLASSES,
    LOCAL_DOUBLE_TEST_COUNTS,
    validate_evidence,
    validate_local_double_reports,
    validate_reports,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
HOST_RUNNER = REPOSITORY_ROOT / "tools/quality-gates/bin/run-java-legacy-it-guarded.sh"
A_SUPERVISOR = (
    REPOSITORY_ROOT / "tools/quality-gates/bin/java-legacy-it-a-supervisor.sh"
)
TOOL_POLICY = (
    REPOSITORY_ROOT / "tools/quality-gates/policy/java-legacy-it-tools-v1.json"
)
POM = REPOSITORY_ROOT / "java-ecosystem/pom.xml"
PIPELINE_LOGBACK = (
    REPOSITORY_ROOT
    / "java-ecosystem/services/pipeline-agent/src/main/resources/logback-spring.xml"
)
WEB_LOGBACK = (
    REPOSITORY_ROOT
    / "java-ecosystem/services/web-server/src/main/resources/logback-spring.xml"
)
GUARDED_MOCK_MAKER = (
    REPOSITORY_ROOT
    / "java-ecosystem/quality/guarded-test-runtime/mockito-extensions/"
    "org.mockito.plugins.MockMaker"
)
GUARDED_MEMBER_ACCESSOR = (
    REPOSITORY_ROOT
    / "java-ecosystem/quality/guarded-test-runtime/mockito-extensions/"
    "org.mockito.plugins.MemberAccessor"
)
PIPELINE_GUARDED_BASE = (
    REPOSITORY_ROOT
    / "java-ecosystem/services/pipeline-agent/src/it/java/org/rostilos/codecrow/"
    "pipelineagent/BasePipelineAgentIT.java"
)
WEB_GUARDED_BASE = (
    REPOSITORY_ROOT
    / "java-ecosystem/services/web-server/src/it/java/org/rostilos/codecrow/"
    "webserver/BaseWebServerIT.java"
)
GUARDED_APPLICATION_PROPERTIES = (
    REPOSITORY_ROOT
    / "java-ecosystem/services/pipeline-agent/src/it/resources/application-it.properties",
    REPOSITORY_ROOT
    / "java-ecosystem/services/web-server/src/it/resources/application-it.properties",
)
RUN_ID = "p007_0123456789abcdef01234567"
CONTAINER_ID = "a" * 64


def _write_queue_evidence(root: Path) -> Namespace:
    reports = root / "reports"
    reports.mkdir()
    counts = {
        "org.rostilos.codecrow.queue.ConnectionFactoryIT": 2,
        "org.rostilos.codecrow.queue.QueueIsolationIT": 1,
        "org.rostilos.codecrow.queue.RedisQueueIT": 8,
    }
    for class_name, count in counts.items():
        suite = ET.Element("testsuite", tests=str(count), failures="0", errors="0")
        for index in range(count):
            ET.SubElement(
                suite,
                "testcase",
                classname=class_name,
                name=f"test_{index}",
            )
        ET.ElementTree(suite).write(
            reports / f"TEST-{class_name}.xml",
            encoding="utf-8",
            xml_declaration=True,
        )
    receipt = root / "provisioning.receipt"
    receipt.write_text(
        "\n".join(
            (
                "schemaVersion=1",
                f"runId={RUN_ID}",
                "lane=queue",
                "targetArtifact=codecrow-queue",
                "namespace=codecrow-p007-0123456789abcdef01234567-queue",
                "policySha256="
                "c79a437923ecfbbedfd2f7a369dc7e71a5caa6f2d119595615ca152f4805cb59",
                "imageManifestSha256="
                "a0c1f1063fadb33cc486760abeeb0edd2a1889c790ac69e9a1a12529cf3ae71c",
                "imageReference=redis@sha256:"
                "6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99",
                f"containerId={CONTAINER_ID}",
                "serviceHost=127.0.0.1",
                "servicePort=16379",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    ledger = root / f"legacy-container-it-queue-{RUN_ID}.json"
    ledger.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "live_call_count": 0,
                "simulated_call_count": 0,
                "calls": [
                    {
                        "boundary": "network",
                        "live": False,
                        "operation": "connect",
                        "outcome": "blocked",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    absence = root / "absence.txt"
    absence.write_text(f"absent {CONTAINER_ID}\n", encoding="utf-8")
    pulls = root / "pulls.log"
    pulls.write_bytes(b"")
    container_report = root / "container.json"
    container_report.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "runId": RUN_ID,
                "lane": "queue",
                "namespace": "codecrow-p007-0123456789abcdef01234567-queue",
                "containerId": CONTAINER_ID,
                "imageReference": "redis@sha256:"
                "6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99",
            }
        ),
        encoding="utf-8",
    )
    return Namespace(
        lane="queue",
        run_id=RUN_ID,
        expected_classes=3,
        expected_tests=11,
        report_directory=reports,
        ledger=ledger,
        receipt=receipt,
        container_report=container_report,
        absence_report=absence,
        pull_events=pulls,
    )


def test_guarded_wrapper_uses_exact_host_a_b_capability_boundaries() -> None:
    host = HOST_RUNNER.read_text(encoding="utf-8")
    supervisor = A_SUPERVISOR.read_text(encoding="utf-8")
    pom = POM.read_text(encoding="utf-8")

    assert "docker pull" not in host
    assert "docker push" not in host
    assert "--pull never" in host
    assert re.search(
        r'if \[\[ "\$LANE" == queue \]\]; then.*?--user redis:redis.*?redis-server',
        host,
        flags=re.DOTALL,
    )
    assert 'cd -- "$HOST_PROXY_DIRECTORY"' in host
    assert "UNIX-LISTEN:service.sock" in host
    assert "UNIX-LISTEN:${SOCKET_PATH}" not in host
    assert re.search(
        r'else\s+/usr/bin/docker.*?--user postgres:postgres.*?'
        r'uid=70,gid=70,mode=0700.*?'
        r'/var/run/postgresql:.*?uid=70,gid=70,mode=0775',
        host,
        flags=re.DOTALL,
    )
    assert "--net=none" in host
    assert "--pidns" in host
    assert "--state-dir=" in host
    assert 'ROOTLESSKIT_STATE="/tmp/codecrow-p007-rk-${RUN_TOKEN}-${LANE}"' in host
    assert 'ROOTLESSKIT_STATE="$TASK_ROOT/' not in host
    assert '/usr/bin/rm -rf -- "$ROOTLESSKIT_STATE"' in host
    assert "validate-p007-maven-cache.sh" in host
    assert "pkill" not in host and "killall" not in host
    assert "--unshare-all" in supervisor
    assert "--share-net" in supervisor
    assert "--disable-userns" in supervisor
    assert "/bin/bash -p -c \"$CLOSE_INHERITED_FDS\"" in supervisor
    assert "exec {descriptor}>&-" in supervisor
    assert 'exec "$@"' in supervisor
    assert "--cap-drop ALL" in supervisor
    assert "--tmpfs /run" in supervisor
    assert "--tmpfs \"$REPOSITORY_ROOT/.llm-handoff-artifacts\"" in supervisor
    assert "--setenv LOGGING_FILE_NAME /codecrow-artifacts/application.log" in supervisor
    assert (
        "--setenv LOGGING_FILE_PATTERN "
        "'/codecrow-artifacts/application-%d{yyyy-MM-dd}.log'"
        in supervisor
    )
    for logback in (PIPELINE_LOGBACK, WEB_LOGBACK):
        body = logback.read_text(encoding="utf-8")
        assert "${LOGGING_FILE_NAME:-logs/" in body
        assert "${LOGGING_FILE_PATTERN:-logs/" in body
    assert "/usr/lib/jvm/java-17-openjdk-amd64" not in supervisor
    assert '--setenv JAVA_HOME "$JAVA_HOME_ROOT"' in supervisor
    assert (
        '--settings "$REPOSITORY_ROOT/tools/offline-harness/maven/settings-ci.xml"'
        in supervisor
    )
    assert "--projects \"$MODULE\"" in supervisor
    assert "--also-make" not in supervisor
    assert pom.count(
        "${maven.multiModuleProjectDirectory}/quality/guarded-test-runtime"
    ) == 3
    assert GUARDED_MOCK_MAKER.read_text(encoding="utf-8") == "mock-maker-subclass\n"
    assert (
        GUARDED_MEMBER_ACCESSOR.read_text(encoding="utf-8")
        == "member-accessor-reflection\n"
    )
    for guarded_base in (PIPELINE_GUARDED_BASE, WEB_GUARDED_BASE):
        assert "@DirtiesContext" not in guarded_base.read_text(encoding="utf-8")
    for properties in GUARDED_APPLICATION_PROPERTIES:
        body = properties.read_text(encoding="utf-8")
        assert "server.address=127.0.0.1" in body
        assert "codecrow.rag.api.enabled=false" in body
        assert "codecrow.rag.api.url=http://127.0.0.1:19999" in body
        assert "codecrow.email.enabled=false" in body
        assert "codecrow.mcp.client.enabled=false" in body
        assert "localhost:" not in body
        assert "codecrow.rag-pipeline." not in body
        assert "codecrow.inference-orchestrator.base-url" not in body
    web_properties = GUARDED_APPLICATION_PROPERTIES[1].read_text(encoding="utf-8")
    assert "codecrow.internal.api.secret=test-internal-secret" in web_properties
    assert "codecrow.security.internalApiSecret" not in web_properties
    assert "llm.sync.scheduler.enabled=false" in web_properties
    assert "llm.sync.openrouter.enabled=false" in web_properties
    assert "CODECROW_LEGACY_IT_EXECUTOR>maven-failsafe" in pom
    assert pom.count("<id>p007-guarded-") == 3
    assert "<id>p007-integration-only</id>" in pom
    assert "@{surefireArgLine}" in pom and "@{failsafeArgLine}" in pom


def test_guarded_tool_policy_matches_the_runtime_attestation_contract() -> None:
    policy = json.loads(TOOL_POLICY.read_text(encoding="utf-8"))
    host = HOST_RUNNER.read_text(encoding="utf-8")
    assert policy["schemaVersion"] == 1
    assert policy["policyId"] == "java-legacy-it-tools-v1"
    assert policy["trustContract"] == {
        "canonicalSystemPath": True,
        "requiredOwnerUid": 0,
        "forbidGroupOrWorldWrite": True,
        "requireExecutableRegularFile": True,
        "recordRuntimeSha256": True,
    }
    declared = {entry["path"] for entry in policy["tools"]}
    assert len(declared) == 7
    for path in declared:
        assert re.search(rf"^  {re.escape(path)}$", host, re.MULTILINE)
    assert 'sha256sum "$tool"' in host
    assert "stat -Lc '%u'" in host


def test_guarded_evidence_validator_accepts_the_exact_queue_census(tmp_path: Path) -> None:
    args = _write_queue_evidence(tmp_path)
    validate_evidence(args)
    assert set(LANE_CLASSES) == {"queue", "pipeline", "web"}


def test_guarded_report_validator_aggregates_nested_junit_class_reports(
    tmp_path: Path,
) -> None:
    counts = {
        "org.rostilos.codecrow.pipelineagent.BranchResolverFlowIT": (5,),
        "org.rostilos.codecrow.pipelineagent.HealthCheckControllerIT": (3,),
        "org.rostilos.codecrow.pipelineagent.LineTrackingFlowIT": (1,),
        "org.rostilos.codecrow.pipelineagent.PipelineActionControllerIT": (4, 5),
        "org.rostilos.codecrow.pipelineagent.PipelineAgentSecurityIT": (5, 3),
        "org.rostilos.codecrow.pipelineagent.ProviderWebhookControllerIT": (4, 3),
        "org.rostilos.codecrow.pipelineagent.RagIndexingControllerIT": (3, 4),
    }
    nested_names = ("FirstGroup", "SecondGroup")
    for outer_class, groups in counts.items():
        if len(groups) > 1:
            empty_suite = ET.Element(
                "testsuite",
                name=outer_class,
                tests="0",
                failures="0",
                errors="0",
                skipped="0",
            )
            ET.ElementTree(empty_suite).write(
                tmp_path / f"TEST-{outer_class}.xml",
                encoding="utf-8",
                xml_declaration=True,
            )
        for group_index, count in enumerate(groups):
            class_name = outer_class
            if len(groups) > 1:
                class_name = f"{outer_class}${nested_names[group_index]}"
            suite = ET.Element(
                "testsuite",
                name=class_name,
                tests=str(count),
                failures="0",
                errors="0",
                skipped="0",
            )
            for test_index in range(count):
                ET.SubElement(
                    suite,
                    "testcase",
                    classname=class_name,
                    name=f"test_{test_index}",
                )
            ET.ElementTree(suite).write(
                tmp_path / f"TEST-{class_name}.xml",
                encoding="utf-8",
                xml_declaration=True,
            )

    validate_reports(tmp_path, "pipeline", expected_classes=7, expected_tests=40)


@pytest.mark.parametrize(
    "mutation,expected",
    (
        ("pull", "pull event"),
        ("absence", "absence"),
        ("receipt", "identity mismatch"),
        ("skip", "failure, error, or skip"),
        ("extra-report", "report count"),
        ("live", "live call"),
        ("container-report", "container report mismatch"),
    ),
)
def test_guarded_evidence_validator_fails_closed(
    tmp_path: Path,
    mutation: str,
    expected: str,
) -> None:
    args = _write_queue_evidence(tmp_path)
    if mutation == "pull":
        args.pull_events.write_text("unexpected pull\n", encoding="utf-8")
    elif mutation == "absence":
        args.absence_report.write_text("absent wrong\n", encoding="utf-8")
    elif mutation == "receipt":
        args.receipt.write_text(
            args.receipt.read_text(encoding="utf-8").replace("lane=queue", "lane=web"),
            encoding="utf-8",
        )
    elif mutation == "skip":
        report = next(args.report_directory.glob("TEST-*.xml"))
        tree = ET.parse(report)
        ET.SubElement(tree.getroot().find("testcase"), "skipped")
        tree.write(report, encoding="utf-8", xml_declaration=True)
    elif mutation == "extra-report":
        (args.report_directory / "TEST-extra.xml").write_text(
            "<testsuite><testcase classname='extra' name='extra'/></testsuite>",
            encoding="utf-8",
        )
    elif mutation == "live":
        document = json.loads(args.ledger.read_text(encoding="utf-8"))
        document["live_call_count"] = 1
        args.ledger.write_text(json.dumps(document), encoding="utf-8")
    elif mutation == "container-report":
        document = json.loads(args.container_report.read_text(encoding="utf-8"))
        document["lane"] = "web"
        args.container_report.write_text(json.dumps(document), encoding="utf-8")
    else:  # pragma: no cover - parametrization is exhaustive.
        raise AssertionError(mutation)

    with pytest.raises(EvidenceError, match=expected):
        validate_evidence(args)


def test_local_double_report_validator_enforces_exact_11_class_65_test_census(
    tmp_path: Path,
) -> None:
    directories = [tmp_path / f"module-{index}" for index in range(5)]
    for directory in directories:
        directory.mkdir()
    for index, (class_name, count) in enumerate(LOCAL_DOUBLE_TEST_COUNTS.items()):
        suite = ET.Element(
            "testsuite",
            tests=str(count),
            failures="0",
            errors="0",
            skipped="0",
        )
        for test_index in range(count):
            ET.SubElement(
                suite,
                "testcase",
                classname=class_name,
                name=f"test_{test_index}",
            )
        ET.ElementTree(suite).write(
            directories[index % len(directories)] / f"TEST-{class_name}.xml",
            encoding="utf-8",
            xml_declaration=True,
        )

    validate_local_double_reports(directories)
    report = next(directories[0].glob("TEST-*.xml"))
    tree = ET.parse(report)
    tree.getroot().set("tests", "999")
    tree.write(report, encoding="utf-8", xml_declaration=True)
    with pytest.raises(EvidenceError, match="declared"):
        validate_local_double_reports(directories)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("crlf", "canonical LF"),
        ("encoding", "must be UTF-8"),
        ("field-count", "missing or extra field"),
        ("field-shape", "not canonical"),
        ("schema", "unsupported"),
        ("target", "target mismatch"),
        ("namespace", "namespace mismatch"),
        ("policy", "policy digest"),
        ("manifest", "manifest digest"),
        ("image", "runtime image"),
        ("container", "container identity"),
        ("endpoint", "service endpoint"),
    ),
)
def test_guarded_receipt_rejects_every_identity_boundary(
    tmp_path: Path, mutation: str, message: str
) -> None:
    args = _write_queue_evidence(tmp_path)
    text = args.receipt.read_text(encoding="utf-8")
    replacements = {
        "schema": ("schemaVersion=1", "schemaVersion=2"),
        "target": ("targetArtifact=codecrow-queue", "targetArtifact=wrong"),
        "namespace": ("namespace=codecrow-", "namespace=wrong-"),
        "policy": ("policySha256=c79a", "policySha256=0000"),
        "manifest": ("imageManifestSha256=a0c1", "imageManifestSha256=0000"),
        "image": ("imageReference=redis@", "imageReference=wrong@"),
        "container": (f"containerId={CONTAINER_ID}", "containerId=bad"),
        "endpoint": ("serviceHost=127.0.0.1", "serviceHost=localhost"),
    }
    if mutation == "crlf":
        args.receipt.write_bytes(text.replace("\n", "\r\n").encode())
    elif mutation == "encoding":
        args.receipt.write_bytes(b"\xff")
    elif mutation == "field-count":
        args.receipt.write_text("\n".join(text.splitlines()[:-1]) + "\n", encoding="utf-8")
    elif mutation == "field-shape":
        args.receipt.write_text(text.replace("schemaVersion=1", "schemaVersion:1"), encoding="utf-8")
    else:
        old, new = replacements[mutation]
        args.receipt.write_text(text.replace(old, new), encoding="utf-8")
    with pytest.raises(EvidenceError, match=message):
        legacy.parse_receipt(args.receipt, "queue", RUN_ID)


def test_guarded_evidence_helpers_reject_file_report_and_ledger_shapes(
    tmp_path: Path,
) -> None:
    with pytest.raises(EvidenceError, match="regular file"):
        legacy._regular_file(tmp_path / "missing", "evidence")
    with pytest.raises(EvidenceError, match="report count"):
        legacy._validate_report_paths([], {})

    args = _write_queue_evidence(tmp_path)
    first = next(args.report_directory.glob("TEST-*.xml"))
    tree = ET.parse(first)
    root = tree.getroot()
    first_class = root.find("testcase").attrib["classname"]
    ET.SubElement(root, "testcase", classname="another.Class", name="extra")
    root.set("tests", str(int(root.attrib["tests"]) + 1))
    tree.write(first, encoding="utf-8", xml_declaration=True)
    with pytest.raises(EvidenceError, match="exactly one class"):
        legacy._validate_report_paths([first], {first_class: int(root.attrib["tests"])})

    tree = ET.parse(first)
    for case in tree.getroot().findall("testcase"):
        case.set("classname", first_class)
    tree.write(first, encoding="utf-8", xml_declaration=True)
    with pytest.raises(EvidenceError, match="duplicate Failsafe class"):
        legacy._validate_report_paths(
            [first, first], {first_class: int(tree.getroot().attrib["tests"])}
        )

    with pytest.raises(EvidenceError, match="report count"):
        legacy._validate_report_paths([first], {"unowned.Class": 1})

    wrong_name = args.report_directory / "TEST-wrong.xml"
    wrong_name.write_bytes(first.read_bytes())
    with pytest.raises(EvidenceError, match="identity mismatch"):
        legacy._validate_report_paths(
            [wrong_name], {first_class: int(tree.getroot().attrib["tests"])}
        )

    malformed = ET.parse(first)
    del malformed.getroot().attrib["tests"]
    malformed.write(first, encoding="utf-8", xml_declaration=True)
    with pytest.raises(EvidenceError, match="census is malformed"):
        legacy._validate_report_paths([first], {first_class: 1})

    valid = ET.Element("testsuite", tests="1", failures="0", errors="0", skipped="0")
    ET.SubElement(valid, "testcase", classname=first_class, name="one")
    ET.ElementTree(valid).write(first, encoding="utf-8", xml_declaration=True)
    with pytest.raises(EvidenceError, match="per-class test census mismatch"):
        legacy._validate_report_paths([first], {first_class: 2})

    empty_directory = tmp_path / "empty"
    empty_directory.mkdir()
    with pytest.raises(EvidenceError, match="census mismatch"):
        validate_reports(empty_directory, "queue", 2, 11)
    not_directory = tmp_path / "not-directory"
    not_directory.write_text("x", encoding="utf-8")
    with pytest.raises(EvidenceError, match="directory is missing"):
        validate_reports(not_directory, "queue", 3, 11)
    with pytest.raises(EvidenceError, match="directory inventory"):
        validate_local_double_reports([empty_directory])
    other_directories = [tmp_path / f"other-{index}" for index in range(3)]
    for directory in other_directories:
        directory.mkdir()
    with pytest.raises(EvidenceError, match="missing or symlinked"):
        validate_local_double_reports(
            [empty_directory, *other_directories, not_directory]
        )

    document = json.loads(args.ledger.read_text(encoding="utf-8"))
    document["schema_version"] = "2.0"
    args.ledger.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(EvidenceError, match="schema mismatch"):
        legacy.validate_ledger(args.ledger)
    document["schema_version"] = "1.0"
    document["calls"] = ["not-an-object"]
    args.ledger.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(EvidenceError, match="calls are malformed"):
        legacy.validate_ledger(args.ledger)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("lane", "unknown", "unknown guarded"),
        ("run_id", "bad", "run id"),
        ("expected_classes", 2, "class census"),
    ),
)
def test_guarded_evidence_rejects_invalid_top_level_identity(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    args = _write_queue_evidence(tmp_path)
    setattr(args, field, value)
    with pytest.raises(EvidenceError, match=message):
        validate_evidence(args)


def test_java_legacy_cli_covers_guarded_local_success_failure_and_entrypoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    guarded_root = tmp_path / "guarded"
    guarded_root.mkdir()
    guarded = _write_queue_evidence(guarded_root)
    monkeypatch.setattr(legacy, "validate_evidence", lambda args: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "java-legacy-it",
            "guarded",
            "--lane", guarded.lane,
            "--run-id", guarded.run_id,
            "--expected-classes", str(guarded.expected_classes),
            "--expected-tests", str(guarded.expected_tests),
            "--report-directory", str(guarded.report_directory),
            "--ledger", str(guarded.ledger),
            "--receipt", str(guarded.receipt),
            "--container-report", str(guarded.container_report),
            "--absence-report", str(guarded.absence_report),
            "--pull-events", str(guarded.pull_events),
        ],
    )
    assert legacy.main() == 0
    assert "PASS" in capsys.readouterr().out

    local = tmp_path / "local"
    local.mkdir()
    monkeypatch.setattr(legacy, "validate_local_double_reports", lambda paths: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["java-legacy-it", "local-double", "--report-directory", str(local)],
    )
    assert legacy.main() == 0

    def fail(paths: list[Path]) -> None:
        raise EvidenceError("simulated failure")

    monkeypatch.setattr(legacy, "validate_local_double_reports", fail)
    assert legacy.main() == 1
    assert "simulated failure" in capsys.readouterr().err

    monkeypatch.setattr(sys, "argv", ["java-legacy-it", "--help"])
    with pytest.raises(SystemExit) as exit_error:
        runpy.run_path(legacy.__file__, run_name="__main__")
    assert exit_error.value.code == 0

#!/usr/bin/python3
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


LANE_CLASSES = {
    "queue": {
        "org.rostilos.codecrow.queue.ConnectionFactoryIT",
        "org.rostilos.codecrow.queue.QueueIsolationIT",
        "org.rostilos.codecrow.queue.RedisQueueIT",
    },
    "pipeline": {
        "org.rostilos.codecrow.pipelineagent.BranchResolverFlowIT",
        "org.rostilos.codecrow.pipelineagent.HealthCheckControllerIT",
        "org.rostilos.codecrow.pipelineagent.LineTrackingFlowIT",
        "org.rostilos.codecrow.pipelineagent.PipelineActionControllerIT",
        "org.rostilos.codecrow.pipelineagent.PipelineAgentSecurityIT",
        "org.rostilos.codecrow.pipelineagent.ProviderWebhookControllerIT",
        "org.rostilos.codecrow.pipelineagent.RagIndexingControllerIT",
    },
    "web": {
        "org.rostilos.codecrow.webserver.AuthControllerIT",
        "org.rostilos.codecrow.webserver.HealthCheckControllerIT",
        "org.rostilos.codecrow.webserver.InternalApiSecurityIT",
        "org.rostilos.codecrow.webserver.LlmModelControllerIT",
        "org.rostilos.codecrow.webserver.ProjectControllerIT",
        "org.rostilos.codecrow.webserver.PublicSiteConfigControllerIT",
        "org.rostilos.codecrow.webserver.QualityGateControllerIT",
        "org.rostilos.codecrow.webserver.TaskManagementControllerIT",
        "org.rostilos.codecrow.webserver.UserDataControllerIT",
        "org.rostilos.codecrow.webserver.WorkspaceControllerIT",
    },
}
LANE_TEST_COUNTS = {
    "queue": {
        "org.rostilos.codecrow.queue.ConnectionFactoryIT": 2,
        "org.rostilos.codecrow.queue.QueueIsolationIT": 1,
        "org.rostilos.codecrow.queue.RedisQueueIT": 8,
    },
    "pipeline": {
        "org.rostilos.codecrow.pipelineagent.BranchResolverFlowIT": 5,
        "org.rostilos.codecrow.pipelineagent.HealthCheckControllerIT": 3,
        "org.rostilos.codecrow.pipelineagent.LineTrackingFlowIT": 1,
        "org.rostilos.codecrow.pipelineagent.PipelineActionControllerIT": 9,
        "org.rostilos.codecrow.pipelineagent.PipelineAgentSecurityIT": 8,
        "org.rostilos.codecrow.pipelineagent.ProviderWebhookControllerIT": 7,
        "org.rostilos.codecrow.pipelineagent.RagIndexingControllerIT": 7,
    },
    "web": {
        "org.rostilos.codecrow.webserver.AuthControllerIT": 18,
        "org.rostilos.codecrow.webserver.HealthCheckControllerIT": 2,
        "org.rostilos.codecrow.webserver.InternalApiSecurityIT": 4,
        "org.rostilos.codecrow.webserver.LlmModelControllerIT": 9,
        "org.rostilos.codecrow.webserver.ProjectControllerIT": 20,
        "org.rostilos.codecrow.webserver.PublicSiteConfigControllerIT": 3,
        "org.rostilos.codecrow.webserver.QualityGateControllerIT": 12,
        "org.rostilos.codecrow.webserver.TaskManagementControllerIT": 10,
        "org.rostilos.codecrow.webserver.UserDataControllerIT": 17,
        "org.rostilos.codecrow.webserver.WorkspaceControllerIT": 17,
    },
}
LOCAL_DOUBLE_TEST_COUNTS = {
    "org.rostilos.codecrow.analysisengine.AiClientIT": 5,
    "org.rostilos.codecrow.email.EmailDeliveryIT": 6,
    "org.rostilos.codecrow.email.service.TemplateRenderingIT": 5,
    "org.rostilos.codecrow.ragengine.RagPipelineClientIT": 6,
    "org.rostilos.codecrow.security.JwtValidationIT": 7,
    "org.rostilos.codecrow.security.TokenEncryptionIT": 6,
    "org.rostilos.codecrow.vcsclient.BitbucketClientIT": 6,
    "org.rostilos.codecrow.vcsclient.GitHubClientIT": 8,
    "org.rostilos.codecrow.vcsclient.GitLabClientIT": 5,
    "org.rostilos.codecrow.vcsclient.VcsClientErrorHandlingIT": 6,
    "org.rostilos.codecrow.vcsclient.refresh.TokenRefreshIT": 5,
}
RUN_ID = re.compile(r"^p007_[0-9a-f]{24}$")
CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")
GUARDED_POLICY_SHA256 = (
    "c79a437923ecfbbedfd2f7a369dc7e71a5caa6f2d119595615ca152f4805cb59"
)
RECEIPT_KEYS = (
    "schemaVersion",
    "runId",
    "lane",
    "targetArtifact",
    "namespace",
    "policySha256",
    "imageManifestSha256",
    "imageReference",
    "containerId",
    "serviceHost",
    "servicePort",
)


class EvidenceError(ValueError):
    pass


def _regular_file(path: Path, description: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise EvidenceError(f"{description} must be one regular file")
    return path


def parse_receipt(path: Path, lane: str, run_id: str) -> dict[str, str]:
    try:
        text = _regular_file(path, "provisioning receipt").read_bytes().decode("utf-8")
    except UnicodeDecodeError as failure:
        raise EvidenceError("provisioning receipt must be UTF-8") from failure
    if "\r" in text or not text.endswith("\n"):
        raise EvidenceError("provisioning receipt must use canonical LF lines")
    lines = text.splitlines()
    if len(lines) != len(RECEIPT_KEYS):
        raise EvidenceError("provisioning receipt has a missing or extra field")
    values: dict[str, str] = {}
    for expected_key, line in zip(RECEIPT_KEYS, lines, strict=True):
        key, separator, value = line.partition("=")
        if separator != "=" or key != expected_key or not value:
            raise EvidenceError("provisioning receipt is not canonical")
        values[key] = value
    if values["schemaVersion"] != "1":
        raise EvidenceError("unsupported provisioning receipt schema")
    if values["lane"] != lane or values["runId"] != run_id:
        raise EvidenceError("provisioning receipt identity mismatch")
    expected_artifact = {
        "queue": "codecrow-queue",
        "pipeline": "codecrow-pipeline-agent",
        "web": "codecrow-web-server",
    }[lane]
    if values["targetArtifact"] != expected_artifact:
        raise EvidenceError("provisioning receipt target mismatch")
    if values["namespace"] != f"codecrow-p007-{run_id.removeprefix('p007_')}-{lane}":
        raise EvidenceError("provisioning receipt namespace mismatch")
    if values["policySha256"] != GUARDED_POLICY_SHA256:
        raise EvidenceError("provisioning policy digest mismatch")
    if values["imageManifestSha256"] != (
        "a0c1f1063fadb33cc486760abeeb0edd2a1889c790ac69e9a1a12529cf3ae71c"
    ):
        raise EvidenceError("image manifest digest mismatch")
    expected_image = (
        "redis@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99"
        if lane == "queue"
        else "postgres@sha256:e013e867e712fec275706a6c51c966f0bb0c93cfa8f51000f85a15f9865a28cb"
    )
    expected_port = "16379" if lane == "queue" else "15432"
    if values["imageReference"] != expected_image:
        raise EvidenceError("runtime image identity mismatch")
    if not CONTAINER_ID.fullmatch(values["containerId"]):
        raise EvidenceError("owned container identity is invalid")
    if values["serviceHost"] != "127.0.0.1" or values["servicePort"] != expected_port:
        raise EvidenceError("guarded service endpoint mismatch")
    return values


def _validate_report_paths(
    reports: list[Path],
    expected_test_counts: dict[str, int],
) -> None:
    if not reports:
        raise EvidenceError("Failsafe report count mismatch")
    actual_test_counts: dict[str, int] = {}
    seen_report_classes: set[str] = set()
    for report in reports:
        _regular_file(report, "Failsafe report")
        root = ET.parse(report).getroot()
        testcases = root.findall(".//testcase")
        report_classes = {case.attrib.get("classname", "") for case in testcases}
        if "" in report_classes or len(report_classes) > 1:
            raise EvidenceError("Failsafe report does not represent exactly one class")
        class_name = (
            next(iter(report_classes))
            if report_classes
            else root.attrib.get("name", "")
        )
        if not class_name or class_name in seen_report_classes:
            raise EvidenceError("duplicate Failsafe class report")
        seen_report_classes.add(class_name)
        outer_class = class_name.partition("$")[0]
        if outer_class not in expected_test_counts:
            raise EvidenceError("Failsafe report count mismatch")
        if report.name != f"TEST-{class_name}.xml":
            raise EvidenceError("Failsafe report identity mismatch")
        if any(case.find(tag) is not None for case in testcases for tag in (
            "failure",
            "error",
            "skipped",
        )):
            raise EvidenceError("Failsafe result contains failure, error, or skip")
        try:
            declared_tests = int(root.attrib["tests"])
            declared_failures = int(root.attrib.get("failures", "0"))
            declared_errors = int(root.attrib.get("errors", "0"))
            declared_skipped = int(root.attrib.get("skipped", "0"))
        except (KeyError, ValueError) as failure:
            raise EvidenceError("Failsafe report census is malformed") from failure
        if (
            declared_tests != len(testcases)
            or declared_failures != 0
            or declared_errors != 0
            or declared_skipped != 0
        ):
            raise EvidenceError("Failsafe report declared a failure, error, or skip")
        actual_test_counts[outer_class] = (
            actual_test_counts.get(outer_class, 0) + len(testcases)
        )
    if actual_test_counts != expected_test_counts:
        raise EvidenceError("Failsafe class or per-class test census mismatch")


def validate_reports(
    directory: Path,
    lane: str,
    expected_classes: int,
    expected_tests: int,
) -> None:
    if directory.is_symlink() or not directory.is_dir():
        raise EvidenceError("Failsafe report directory is missing or symlinked")
    if expected_classes != len(LANE_TEST_COUNTS[lane]) \
            or expected_tests != sum(LANE_TEST_COUNTS[lane].values()):
        raise EvidenceError("Failsafe test census mismatch")
    _validate_report_paths(
        sorted(directory.glob("TEST-*.xml")),
        LANE_TEST_COUNTS[lane],
    )


def validate_local_double_reports(directories: list[Path]) -> None:
    if len(directories) != 5 or len(set(directories)) != 5:
        raise EvidenceError("local-double report directory inventory mismatch")
    reports: list[Path] = []
    for directory in directories:
        if directory.is_symlink() or not directory.is_dir():
            raise EvidenceError("local-double report directory is missing or symlinked")
        reports.extend(directory.glob("TEST-*.xml"))
    _validate_report_paths(sorted(reports), LOCAL_DOUBLE_TEST_COUNTS)


def validate_ledger(path: Path) -> None:
    document = json.loads(_regular_file(path, "external-call ledger").read_text("utf-8"))
    if document.get("schema_version") != "1.0":
        raise EvidenceError("external-call ledger schema mismatch")
    if document.get("live_call_count") != 0:
        raise EvidenceError("external-call ledger recorded a live call")
    calls = document.get("calls")
    if not isinstance(calls, list) or any(
        not isinstance(call, dict) or call.get("live") is not False for call in calls
    ):
        raise EvidenceError("external-call ledger calls are malformed or live")


def validate_container_report(path: Path, receipt: dict[str, str]) -> None:
    report = json.loads(_regular_file(path, "owned container report").read_text("utf-8"))
    expected = {
        "schemaVersion": 1,
        "runId": receipt["runId"],
        "lane": receipt["lane"],
        "namespace": receipt["namespace"],
        "containerId": receipt["containerId"],
        "imageReference": receipt["imageReference"],
    }
    if report != expected:
        raise EvidenceError("owned container report mismatch")


def validate_evidence(args: argparse.Namespace) -> None:
    if args.lane not in LANE_CLASSES:
        raise EvidenceError("unknown guarded legacy IT lane")
    if not RUN_ID.fullmatch(args.run_id):
        raise EvidenceError("guarded run id is invalid")
    if args.expected_classes != len(LANE_CLASSES[args.lane]):
        raise EvidenceError("declared class census does not match the lane contract")
    receipt = parse_receipt(args.receipt, args.lane, args.run_id)
    validate_reports(
        args.report_directory,
        args.lane,
        args.expected_classes,
        args.expected_tests,
    )
    validate_ledger(args.ledger)
    validate_container_report(args.container_report, receipt)
    absence = _regular_file(args.absence_report, "container absence report").read_text(
        encoding="utf-8"
    )
    if absence != f"absent {receipt['containerId']}\n":
        raise EvidenceError("container absence report mismatch")
    if _regular_file(args.pull_events, "pull event log").read_bytes() != b"":
        raise EvidenceError("image pull event log is not empty")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    guarded = commands.add_parser("guarded")
    guarded.add_argument("--lane", required=True)
    guarded.add_argument("--run-id", required=True)
    guarded.add_argument("--expected-classes", type=int, required=True)
    guarded.add_argument("--expected-tests", type=int, required=True)
    guarded.add_argument("--report-directory", type=Path, required=True)
    guarded.add_argument("--ledger", type=Path, required=True)
    guarded.add_argument("--receipt", type=Path, required=True)
    guarded.add_argument("--container-report", type=Path, required=True)
    guarded.add_argument("--absence-report", type=Path, required=True)
    guarded.add_argument("--pull-events", type=Path, required=True)
    local_double = commands.add_parser("local-double")
    local_double.add_argument(
        "--report-directory",
        action="append",
        type=Path,
        required=True,
    )
    return parser


def main() -> int:
    try:
        arguments = build_parser().parse_args()
        if arguments.command == "guarded":
            validate_evidence(arguments)
        else:
            validate_local_double_reports(arguments.report_directory)
    except (EvidenceError, ET.ParseError, json.JSONDecodeError, OSError) as failure:
        print(f"ERROR: {failure}", file=sys.stderr)
        return 1
    print("java legacy IT evidence: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

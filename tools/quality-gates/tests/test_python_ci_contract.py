from __future__ import annotations

import configparser
import hashlib
import json
import re
from pathlib import Path


QUALITY_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = QUALITY_ROOT.parents[1]
WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "offline-tests.yml"
CONFIG = QUALITY_ROOT / "config"
POLICY = QUALITY_ROOT / "policy"
CONFIGURATION_CONTRACT_SELECTOR = (
    "tools/quality-gates/tests/test_compensating_configuration_contracts.py::"
    "test_critical_declarative_configuration_contracts"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _coverage_config(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    loaded = parser.read(CONFIG / name, encoding="utf-8")
    assert loaded == [str(CONFIG / name)]
    return parser


def test_frozen_runner_lock_and_p0_01_comparison_base_are_exact() -> None:
    assert _sha256(REPOSITORY_ROOT / "tools/offline-harness/bin/run-offline.sh") == (
        "839d8945913bc385d772b3da3bb9dacc0ff871a4195159ea1ad8a374362ee86f"
    )
    assert _sha256(REPOSITORY_ROOT / "tools/offline-harness/requirements/ci-test.lock") == (
        "d3629cfc00ed139614507929681d67199dc0c66f980f460d939543e3373b84c7"
    )

    attestation = json.loads(
        (POLICY / "comparison-base-v1.json").read_text(encoding="utf-8")
    )
    assert _sha256(POLICY / "comparison-base-v1.json") == (
        "58b54d329ca06db021eb26e2b32a58a20ab6794e39dc6da91224a13e33802666"
    )
    assert attestation["schemaVersion"] == 1
    assert attestation["source"] == {
        "artifact": ".llm-handoff-artifacts/p0-01/baseline-manifest.json",
        "manifestSha256": (
            "be9893de0ad6dc3de087aac21aac11f79b1c8f8962e7184782c53016bacd3c9c"
        ),
        "taskId": "P0-01",
    }
    assert {
        key: attestation["repository"][key] for key in ("headCommit", "id")
    } == {
        "headCommit": "89287e1fce55dc9bffeca2b92ce660d8791ae6ac",
        "id": "codecrow-public",
    }
    dirty = attestation["repository"]["dirtyState"]
    assert dirty["captured"] is True
    assert len(dirty["entries"]) == 15
    assert [entry["path"] for entry in dirty["entries"]] == sorted(
        entry["path"] for entry in dirty["entries"]
    )
    assert {entry["status"] for entry in dirty["entries"]} == {" M", "??"}
    assert all(re.fullmatch(r"[0-9a-f]{64}", entry["contentSha256"])
               for entry in dirty["entries"])


def test_application_and_self_coverage_configuration_is_branch_complete() -> None:
    inference = _coverage_config("inference.coveragerc")
    assert inference.getboolean("run", "branch") is True
    assert inference.getboolean("run", "relative_files") is True
    assert inference.get("run", "source").split() == ["src"]
    assert inference.getboolean("report", "include_namespace_packages") is True
    assert inference.get("run", "omit").split() == ["src/.venv/*"]
    assert inference.get("report", "omit").split() == ["src/.venv/*"]

    rag = _coverage_config("rag.coveragerc")
    assert rag.getboolean("run", "branch") is True
    assert rag.getboolean("run", "relative_files") is True
    assert rag.get("run", "source").split() == ["."]
    omitted = set(rag.get("run", "omit").split())
    assert {".venv/*", "integration/*", "tests/*", "setup.py", "test_api.py"} <= omitted
    assert rag.get("report", "omit").split() == rag.get("run", "omit").split()

    quality = _coverage_config("quality-gates.coveragerc")
    assert quality.getboolean("run", "branch") is True
    assert quality.getboolean("run", "relative_files") is True
    assert quality.get("run", "source").split() == [
        "tools/quality-gates/quality_gates"
    ]


def test_workflow_runs_exact_offline_application_coverage_and_quality_gate() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "fetch-depth: 0" in workflow
    assert "submodules: recursive" in workflow
    assert "persist-credentials: false" in workflow

    for package in ("inference-orchestrator", "rag-pipeline"):
        assert f"cd python-ecosystem/{package}" in workflow
        assert f"config/{'inference' if package.startswith('inference') else 'rag'}.coveragerc" in workflow
    assert workflow.count("--cov-branch") >= 5
    assert workflow.count("--cov-append") == 3
    assert workflow.count("--cov-report=") >= 4
    assert "inference-orchestrator.json" in workflow
    assert "rag-pipeline.json" in workflow
    assert (
        workflow.count(
            "--deselect=tests/test_api_models.py::TestVectorStorageInspectionModels::"
            "test_graph_limits_are_bounded"
        )
        == 0
    )

    for ledger in (
        "inference-unit.json",
        "inference-integration.json",
        "rag-unit.json",
        "rag-integration.json",
    ):
        assert workflow.count(ledger) >= 2

    quality_step = re.search(
        r"name: Run P0-07 quality tooling.*?(?=\n      - name:|\Z)",
        workflow,
        flags=re.DOTALL,
    )
    assert quality_step is not None
    quality_body = quality_step.group(0)
    deselection = f"--deselect={CONFIGURATION_CONTRACT_SELECTOR}"
    assert quality_body.count(deselection) == 1
    assert quality_body.count("--cov-report= \\\n") == 1
    assert "--cov-fail-under=100" not in quality_body
    assert '--cov-report="json:$P007/coverage/quality-gates.json"' not in quality_body
    assert 'totals["covered_lines"] == totals["num_statements"]' not in quality_body

    normalization_step = re.search(
        r"name: Normalize and enforce the P0-07 changed-path coverage gate.*?"
        r"(?=\n      - name:|\Z)",
        workflow,
        flags=re.DOTALL,
    )
    assert normalization_step is not None
    normalization_body = normalization_step.group(0)
    selector_executions = re.findall(
        rf"^\s*{re.escape(CONFIGURATION_CONTRACT_SELECTOR)}$",
        workflow,
        flags=re.MULTILINE,
    )
    assert len(selector_executions) == 1
    selector_index = normalization_body.index(CONFIGURATION_CONTRACT_SELECTOR)
    command_start = normalization_body.rindex(
        "tools/offline-harness/bin/run-offline.sh", 0, selector_index
    )
    dedicated_command = normalization_body[command_start:selector_index]
    for required in (
        "--cov --cov-branch --cov-append",
        "--cov-config=tools/quality-gates/config/quality-gates.coveragerc",
        "--cov-fail-under=100",
        '--cov-report="json:$P007/coverage/quality-gates.json"',
        "--junitxml=.llm-handoff-artifacts/p0-07/receipts/"
        "configuration-contracts.junit.xml",
    ):
        assert dedicated_command.count(required) == 1
    line_assertion = 'totals["covered_lines"] == totals["num_statements"]'
    branch_assertion = 'totals["covered_branches"] == totals["num_branches"]'
    line_assertion_index = normalization_body.index(line_assertion)
    branch_assertion_index = normalization_body.index(branch_assertion)
    normalize_index = normalization_body.index(
        '--input "$P007/coverage/quality-gates.json"'
    )
    assert selector_index < line_assertion_index < normalize_index
    assert selector_index < branch_assertion_index < normalize_index
    assert "pytest-xdist" not in workflow
    assert "--parallel" not in workflow


def test_workflow_prepares_and_consumes_authoritative_java_aggregate() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert workflow.count("quality-coverage") >= 3
    assert workflow.count("-pl quality/coverage-aggregate -am") >= 3
    assert "-DskipITs" not in workflow
    assert "p007-prebuild-without-integration-execution" in workflow
    assert "run-java-legacy-it-guarded.sh" in workflow
    assert "jacoco-aggregate/jacoco.xml" in workflow
    quality_reactor = re.search(
        r"name: Run P0-07 Java quality reactor.*?(?=\n      - name:|\Z)",
        workflow,
        flags=re.DOTALL,
    )
    assert quality_reactor is not None
    body = quality_reactor.group(0)
    assert "tools/quality-gates/bin/run-java-coverage-offline.sh" in body
    assert "CODECROW_P007_CACHE_RECEIPT_SHA256" in body
    assert "p007-integration-only" in body
    assert "p007-aggregate-only" in body
    assert "clean verify" in body
    assert "-T" not in body
    assert "-DskipTests" not in body
    assert "maven.test.skip" not in body
    assert "test.failure.ignore" not in body
    assert "jacoco.skip" not in body
    ledger_binding = 'CODECROW_EXTERNAL_CALL_LEDGER_DIR="$LEDGERS"'
    local_double_start = body.index("LOCAL_DOUBLE_SELECTORS=")
    local_double_validation = body.index(
        "java_legacy_it.py local-double", local_double_start
    )
    local_double_execution = body[local_double_start:local_double_validation]
    assert local_double_execution.count(ledger_binding) == 1
    assert local_double_execution.index(ledger_binding) < local_double_execution.index(
        '"$P007_RUNNER"'
    )
    assert local_double_execution.count(
        "-pl libs/vcs-client,libs/security,libs/email,libs/analysis-engine,libs/rag-engine"
    ) == 1
    assert local_double_execution.count("-am \\\n") == 1
    assert local_double_execution.count(
        "-Dfailsafe.failIfNoSpecifiedTests=false"
    ) == 1
    assert body.count(ledger_binding) == 2
    final_ledger_validation = (
        '"$PYTHON_ENV/bin/python" tools/offline-harness/bin/validate-ledgers.py '
        '"$LEDGERS"'
    )
    assert body.count(final_ledger_validation) == 1
    final_validation_index = body.index(final_ledger_validation)
    assert local_double_validation < final_validation_index
    assert body.index("for lane in queue pipeline web") < final_validation_index
    assert body.index("p007-aggregate-only") < final_validation_index
    assert body.rindex('"$P007_RUNNER"') < final_validation_index


def test_java_profiles_defer_test_support_check_until_final_aggregate() -> None:
    parent = (REPOSITORY_ROOT / "java-ecosystem" / "pom.xml").read_text(
        encoding="utf-8"
    )
    test_support = (
        REPOSITORY_ROOT / "java-ecosystem" / "libs" / "test-support" / "pom.xml"
    ).read_text(encoding="utf-8")

    assert (
        "<p007.test-support-coverage-check.skip>true</"
        "p007.test-support-coverage-check.skip>"
    ) in parent
    for profile_id, expected in (
        ("p007-prebuild-without-integration-execution", "true"),
        ("p007-integration-only", "true"),
        ("p007-aggregate-only", "false"),
    ):
        profile = re.search(
            rf"<profile>\s*<id>{profile_id}</id>(.*?)</profile>",
            parent,
            flags=re.DOTALL,
        )
        assert profile is not None
        assert (
            f"<p007.test-support-coverage-check.skip>{expected}</"
            "p007.test-support-coverage-check.skip>"
        ) in profile.group(1)
        assert (
            f"<p007.test-support-cross-module-merge.skip>{expected}</"
            "p007.test-support-cross-module-merge.skip>"
        ) in profile.group(1)

    assert (
        "<p007.test-support-cross-module-merge.skip>true</"
        "p007.test-support-cross-module-merge.skip>"
    ) in parent

    merge = re.search(
        r"<execution>\s*<id>merge-p007-cross-module-test-support-coverage</id>"
        r"(.*?)</execution>",
        test_support,
        flags=re.DOTALL,
    )
    assert merge is not None
    assert (
        "<skip>${p007.test-support-cross-module-merge.skip}</skip>"
        in merge.group(1)
    )
    assert "${maven.multiModuleProjectDirectory}" in merge.group(1)
    assert "**/target/jacoco-unit.exec" in merge.group(1)
    assert "**/target/jacoco-it.exec" in merge.group(1)

    execution = re.search(
        r"<execution>\s*<id>offline-harness-coverage-check</id>(.*?)</execution>",
        test_support,
        flags=re.DOTALL,
    )
    assert execution is not None
    assert (
        "<skip>${p007.test-support-coverage-check.skip}</skip>"
        in execution.group(1)
    )

    analysis_engine = (
        REPOSITORY_ROOT
        / "java-ecosystem"
        / "libs"
        / "analysis-engine"
        / "pom.xml"
    ).read_text(encoding="utf-8")
    assert "@{argLine}" not in analysis_engine
    assert "@{surefireArgLine}" in analysis_engine


def test_workflow_fails_closed_and_uploads_hidden_p0_07_evidence() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "comparison-base-v1.json" in workflow
    assert "resolve-changes" in workflow
    assert "coverage-baseline-v1.json" in workflow
    assert "include-hidden-files: true" in workflow
    assert "if-no-files-found: error" in workflow
    assert "if: always()" in workflow
    assert ".llm-handoff-artifacts/p0-07/" in workflow
    assert "evidence-sha256.txt" in workflow
    forbidden_fallbacks = ("HEAD^", "origin/main", "pull_request.base.sha")
    assert all(fallback not in workflow for fallback in forbidden_fallbacks)


def test_workflow_source_epoch_trust_boundary_is_ordered_and_data_bound() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    pin_marker = "- name: Pin the complete P0-07 source inventory before coverage execution"
    python_marker = "- name: Run Python harness and guarded component contracts with zero network"
    quality_marker = (
        "- name: Run P0-07 quality tooling coverage base and targeted mutations"
    )
    java_marker = "- name: Run P0-07 Java quality reactor with authoritative aggregate coverage"
    normalize_marker = "- name: Normalize and enforce the P0-07 changed-path coverage gate"
    assert workflow.index(pin_marker) < min(
        workflow.index(python_marker),
        workflow.index(quality_marker),
        workflow.index(java_marker),
        workflow.index(normalize_marker),
    )

    pin_step = re.search(
        rf"{re.escape(pin_marker)}.*?(?=\n      - name:|\Z)",
        workflow,
        flags=re.DOTALL,
    )
    assert pin_step is not None
    pin_body = pin_step.group(0)
    assert "id: p007_source_inventory" in pin_body
    assert "resolve-source-inventory" in pin_body
    assert "pre-test-inventory.json" in pin_body
    assert "inventory_sha256=" in pin_body
    assert "artifact_sha256=" in pin_body
    assert pin_body.index("verify-trust-bundle") < pin_body.index(
        "resolve-source-inventory"
    )
    assert "/usr/bin/sha256sum" in pin_body
    assert "steps.p007_trust_bootstrap.outputs.bundle_sha256" in pin_body
    assert "P007_TRUST_BUNDLE_SHA256" in workflow

    normalize_step = re.search(
        rf"{re.escape(normalize_marker)}.*?(?=\n      - name:|\Z)",
        workflow,
        flags=re.DOTALL,
    )
    assert normalize_step is not None
    normalize_body = normalize_step.group(0)
    inventory_output = "${{ steps.p007_source_inventory.outputs.inventory_sha256 }}"
    artifact_output = "${{ steps.p007_source_inventory.outputs.artifact_sha256 }}"
    assert normalize_body.count(
        f'--source-inventory-sha256 "{inventory_output}"'
    ) == 4
    assert normalize_body.count("normalize-jacoco-aggregate") == 1
    assert normalize_body.count("normalize-coveragepy") == 3
    assert "--include-worktree" in normalize_body
    assert normalize_body.index("resolve-changes") < normalize_body.index("evaluate")
    assert "--source-inventory-policy tools/quality-gates/policy/source-inventory-policy-v1.json" in normalize_body
    assert '--pinned-source-inventory "$P007/source/pre-test-inventory.json"' in normalize_body
    assert (
        f'--pinned-source-inventory-artifact-sha256 "{artifact_output}"'
        in normalize_body
    )
    assert "--correctness-policy tools/quality-gates/policy/correctness-policy-v1.json" in normalize_body
    assert "--base-attestation tools/quality-gates/policy/comparison-base-v1.json" in normalize_body

    revalidate_marker = (
        "- name: Revalidate protected P0-07 evidence immediately before checksums"
    )
    checksum_marker = "- name: Checksum P0-07 quality-gate evidence"
    assert workflow.index(normalize_marker) < workflow.index(revalidate_marker)
    assert workflow.index(revalidate_marker) < workflow.index(checksum_marker)
    revalidate_step = re.search(
        rf"{re.escape(revalidate_marker)}.*?(?=\n      - name:|\Z)",
        workflow,
        flags=re.DOTALL,
    )
    assert revalidate_step is not None
    revalidate_body = revalidate_step.group(0)
    assert "verify-trust-bundle" in revalidate_body
    assert "/usr/bin/python3 -I -S" in revalidate_body
    assert "evaluate" in revalidate_body
    assert '--pinned-source-inventory "$P007/source/pre-test-inventory.json"' in revalidate_body
    assert artifact_output in revalidate_body
    assert "pre-test-inventory-artifact.sha256" in revalidate_body
    assert "final-revalidated-result.json" in revalidate_body
    assert "cmp --silent" in revalidate_body


def test_workflow_authenticates_external_bundle_before_candidate_execution() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    checkout_marker = "- name: Checkout without credentials"
    bootstrap_marker = "- name: Authenticate P0-07 trust bundle before candidate execution"
    setup_java_marker = "- name: Set up Java"
    setup_python_marker = "- name: Set up Python"
    dependency_marker = (
        "- name: Resolve and freeze build dependencies outside application-test isolation"
    )
    final_marker = "- name: Revalidate protected P0-07 evidence immediately before checksums"
    protected_expression = "${{ vars.P007_TRUST_BUNDLE_SHA256 }}"

    assert workflow.index(checkout_marker) < workflow.index(bootstrap_marker)
    assert workflow.index(bootstrap_marker) < min(
        workflow.index(setup_java_marker),
        workflow.index(setup_python_marker),
        workflow.index(dependency_marker),
    )
    job_header = workflow[: workflow.index("    steps:")]
    assert "P007_TRUST_BUNDLE_SHA256:" not in job_header
    assert workflow.count(protected_expression) == 2

    bootstrap_step = re.search(
        rf"{re.escape(bootstrap_marker)}.*?(?=\n      - name:|\Z)",
        workflow,
        flags=re.DOTALL,
    )
    assert bootstrap_step is not None
    bootstrap_body = bootstrap_step.group(0)
    assert "id: p007_trust_bootstrap" in bootstrap_body
    assert (
        f"P007_PROTECTED_TRUST_BUNDLE_SHA256: {protected_expression}"
        in bootstrap_body
    )
    assert "/usr/bin/python3 -I -S" in bootstrap_body
    assert "/usr/bin/sha256sum" in bootstrap_body
    assert "os.O_NOFOLLOW" in bootstrap_body
    assert "bundle_sha256=" in bootstrap_body
    for forbidden in (
        "$GITHUB_ENV",
        "$PYTHON_ENV",
        "PYTHONPATH=tools/quality-gates",
        "-m quality_gates",
        "mvn ",
    ):
        assert forbidden not in bootstrap_body

    final_step = re.search(
        rf"{re.escape(final_marker)}.*?(?=\n      - name:|\Z)",
        workflow,
        flags=re.DOTALL,
    )
    assert final_step is not None
    final_body = final_step.group(0)
    assert (
        f"P007_PROTECTED_TRUST_BUNDLE_SHA256: {protected_expression}"
        in final_body
    )
    assert "/usr/bin/python3 -I -S" in final_body
    assert "/usr/bin/sha256sum" in final_body
    assert "os.O_NOFOLLOW" in final_body
    assert final_body.index("/usr/bin/python3 -I -S") < final_body.index(
        '"${QUALITY[@]}" verify-trust-bundle'
    )
    assert final_body.index('"${QUALITY[@]}" verify-trust-bundle') < final_body.index(
        '"${QUALITY[@]}" evaluate'
    )
    assert "$P007_TRUST_BUNDLE_SHA256" not in final_body


def test_application_pytest_profiles_remain_the_p0_03_guarded_profiles() -> None:
    for package in ("inference-orchestrator", "rag-pipeline"):
        profile = (
            REPOSITORY_ROOT / "python-ecosystem" / package / "pytest.ini"
        ).read_text(encoding="utf-8")
        assert "codecrow_test_harness.pytest_plugin" in profile

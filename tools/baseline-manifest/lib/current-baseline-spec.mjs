import { execFile as execFileCallback } from "node:child_process";
import { promisify } from "node:util";

const execFile = promisify(execFileCallback);

const TEST_COVERAGE_ARGS = [
  "--test",
  "--experimental-test-coverage",
  "--test-coverage-lines=100",
  "--test-coverage-branches=100",
  "--test-coverage-functions=100",
  "--test-coverage-include=tools/baseline-manifest/lib/*.mjs",
  "tools/baseline-manifest/test/*.test.mjs",
];

const PROMPT_FILES = [
  "prompt_constants.py",
  "prompt_builder.py",
  "constants_shared.py",
  "constants_branch.py",
  "constants_mcp.py",
  "constants_stage_0.py",
  "constants_stage_1.py",
  "constants_stage_2.py",
  "constants_stage_3.py",
].map((fileName) => ({
  repository: "codecrow-public",
  path: `python-ecosystem/inference-orchestrator/src/utils/prompts/${fileName}`,
}));

function currentCommands() {
  return [
    {
      id: "handoff-validation",
      workingDirectory: ".",
      argv: ["node", "llm-handoff/validate-handoff.mjs"],
    },
    {
      id: "baseline-manifest-tests",
      workingDirectory: "codecrow-public",
      argv: ["node", ...TEST_COVERAGE_ARGS],
    },
    {
      id: "baseline-manifest-capture",
      workingDirectory: "codecrow-public",
      argv: ["node", "tools/baseline-manifest/bin/capture-current-baseline.mjs"],
    },
    {
      id: "baseline-manifest-verify",
      workingDirectory: "codecrow-public",
      argv: [
        "node",
        "tools/baseline-manifest/bin/verify-baseline.mjs",
        ".llm-handoff-artifacts/p0-01/baseline-manifest.json",
      ],
    },
    {
      id: "java-authoritative-verify",
      workingDirectory: "codecrow-public/java-ecosystem",
      argv: [
        "mvn",
        "-B",
        "--no-transfer-progress",
        "-Dspring.main.banner-mode=off",
        "-Dspring.main.log-startup-info=false",
        "-Dlogging.level.root=WARN",
        "-Dlogging.level.org.rostilos.codecrow=WARN",
        "-Dlogging.level.org.springframework=WARN",
        "-Dlogging.level.org.springframework.security=WARN",
        "-Dlogging.level.org.hibernate=WARN",
        "-Dlogging.level.org.hibernate.SQL=OFF",
        "clean",
        "verify",
        "-T",
        "1C",
      ],
    },
    {
      id: "java-unit-wrapper",
      workingDirectory: "codecrow-public",
      argv: ["./tools/test-suite/unit/java-ecosystem/run-tests.sh"],
    },
    {
      id: "java-unit-coverage-wrapper",
      workingDirectory: "codecrow-public",
      argv: ["./tools/test-suite/unit/java-ecosystem/check-coverage.sh"],
    },
    {
      id: "java-integration-wrapper",
      workingDirectory: "codecrow-public",
      argv: ["./tools/test-suite/integration/java-ecosystem/run-tests.sh"],
    },
    {
      id: "java-integration-coverage-wrapper",
      workingDirectory: "codecrow-public",
      argv: ["./tools/test-suite/integration/java-ecosystem/check-coverage.sh"],
    },
    {
      id: "python-unit-wrapper",
      workingDirectory: "codecrow-public",
      argv: ["./tools/test-suite/unit/python-ecosystem/run-tests.sh"],
    },
    {
      id: "python-unit-coverage-wrapper",
      workingDirectory: "codecrow-public",
      argv: ["./tools/test-suite/unit/python-ecosystem/check-coverage.sh", "--threshold", "80"],
    },
    {
      id: "python-integration-wrapper",
      workingDirectory: "codecrow-public",
      argv: ["./tools/test-suite/integration/python-ecosystem/run-tests.sh"],
    },
    {
      id: "python-integration-coverage-wrapper",
      workingDirectory: "codecrow-public",
      argv: ["./tools/test-suite/integration/python-ecosystem/check-coverage.sh"],
    },
    {
      id: "inference-orchestrator-unit",
      workingDirectory: "codecrow-public/python-ecosystem/inference-orchestrator",
      argv: ["pytest", "tests/"],
    },
    {
      id: "rag-pipeline-unit",
      workingDirectory: "codecrow-public/python-ecosystem/rag-pipeline",
      argv: ["pytest", "tests/"],
    },
    {
      id: "frontend-install",
      workingDirectory: "codecrow-public/frontend",
      argv: ["npm", "ci"],
    },
    {
      id: "frontend-lint",
      workingDirectory: "codecrow-public/frontend",
      argv: ["npm", "run", "lint"],
    },
    {
      id: "frontend-build",
      workingDirectory: "codecrow-public/frontend",
      argv: ["npm", "run", "build"],
    },
    {
      id: "static-install",
      workingDirectory: "codecrow-static",
      argv: ["npm", "ci"],
    },
    {
      id: "static-lint",
      workingDirectory: "codecrow-static",
      argv: ["npm", "run", "lint"],
    },
    {
      id: "static-build",
      workingDirectory: "codecrow-static",
      argv: ["npm", "run", "build"],
    },
    {
      id: "deployable-release-build",
      workingDirectory: "codecrow-public",
      argv: ["env", "CODECROW_DEPLOY_SERVICES=all", "./deployment/ci/ci-build.sh"],
      authorization: "release-ci-only",
      sideEffects: "writes CI configuration and pushes images",
    },
  ];
}

async function executeVersionCommand(command, args) {
  return execFile(command, args, {
    encoding: "utf8",
    env: { ...process.env, NO_COLOR: "1", TERM: "dumb" },
  });
}

function firstVersionLine({ stdout, stderr }) {
  return `${stdout}${stderr}`
    .replaceAll(/\u001b\[[0-9;]*m/gu, "")
    .trim()
    .split("\n")[0];
}

export async function probeRuntimeVersions(run = executeVersionCommand) {
  const probes = [
    ["java", "java", ["-version"]],
    ["maven", "mvn", ["-version"]],
    ["python", "python3", ["--version"]],
    ["node", "node", ["--version"]],
    ["npm", "npm", ["--version"]],
    ["container", "docker", ["--version"]],
  ];
  return Object.fromEntries(
    await Promise.all(
      probes.map(async ([id, command, args]) => [id, firstVersionLine(await run(command, args))]),
    ),
  );
}

export function buildCurrentBaselineSpec({
  artifactRoot,
  capturedAt,
  environment,
  inspectRepository,
  publicRoot,
  runtimes,
  staticRoot,
  workspaceRoot,
}) {
  return {
    artifactRoot,
    capturedAt,
    commands: currentCommands(),
    environment,
    existingArtifacts: [
      { id: "gate-0-start-state", path: "gate-0/start-state.json" },
      { id: "external-call-ledger", path: "gate-0/external-call-ledger.json" },
      { id: "red-manifest-validator", path: "red/manifest-validator.txt" },
      { id: "red-workspace-inspector", path: "red/workspace-inspector.txt" },
      { id: "red-source-input-digest", path: "red/source-input-digest.txt" },
      { id: "red-baseline-capture", path: "red/baseline-capture.txt" },
      { id: "red-independent-review", path: "red/independent-review.txt" },
      { id: "green-unit-and-coverage", path: "green/unit-and-coverage.txt" },
      { id: "green-capture-and-verify", path: "green/capture-and-verify.txt" },
      { id: "independent-review", path: "review/independent-review.txt" },
    ],
    fixtures: [
      { id: "source-audit", repository: "workspace", path: "PR_ANALYSIS_FN_FP_AUDIT_AND_ROADMAP.md" },
      { id: "handoff-validator", repository: "workspace", path: "llm-handoff/validate-handoff.mjs" },
      { id: "benchmark-readme", repository: "workspace", path: "benchmark/README-code-review-benchmark.md" },
      { id: "benchmark-harness", repository: "workspace", path: "benchmark/codecrow_crb_harness.py" },
      { id: "validation-readme", repository: "workspace", path: "codecrow-validation/README.md" },
      { id: "validation-corpus", repository: "workspace", path: "codecrow-validation/branch_issue_validation.csv" },
    ],
    index: {
      status: "UNAVAILABLE",
      version: "legacy-unversioned",
      generationId: null,
      reason: "The current local system exposes no immutable index-generation manifest bound to the source revisions.",
    },
    inspectRepository,
    lockfiles: [
      { repository: "codecrow-public", path: "frontend/package-lock.json", kind: "lockfile" },
      { repository: "codecrow-static", path: "package-lock.json", kind: "lockfile" },
      { repository: "codecrow-public", path: "java-ecosystem/pom.xml", kind: "unlocked-descriptor" },
      {
        repository: "codecrow-public",
        path: "python-ecosystem/inference-orchestrator/src/requirements.txt",
        kind: "unlocked-descriptor",
      },
      {
        repository: "codecrow-public",
        path: "python-ecosystem/rag-pipeline/requirements.txt",
        kind: "unlocked-descriptor",
      },
      {
        repository: "codecrow-public",
        path: "python-ecosystem/rag-pipeline/requirements.local.txt",
        kind: "unlocked-descriptor",
      },
    ],
    modelPolicies: [
      {
        purpose: "review-generation-and-rendering",
        status: "UNCONFIGURED",
        providerId: null,
        modelId: null,
        reason: "Offline baseline capture does not load provider credentials or deployment overrides.",
      },
      {
        purpose: "verification",
        status: "UNCONFIGURED",
        providerId: null,
        modelId: null,
        reason: "Offline baseline capture does not load provider credentials or deployment overrides.",
      },
      {
        purpose: "embedding",
        status: "UNCONFIGURED",
        providerId: null,
        modelId: null,
        reason: "No live embedding provider or index is used by baseline capture.",
      },
    ],
    promptGroups: [{ id: "legacy-review-prompts", files: PROMPT_FILES }],
    randomSeeds: [
      { id: "baseline-evaluation", value: 20260714 },
      { id: "property-and-schedule-tests", value: 424242 },
    ],
    repositories: [
      { id: "codecrow-public", root: publicRoot },
      { id: "codecrow-static", root: staticRoot },
    ],
    ruleGroups: [
      {
        id: "legacy-review-policy-and-deduplication",
        files: [
          { repository: "codecrow-public", path: "python-ecosystem/inference-orchestrator/src/service/review/orchestrator/inference_policy.py" },
          { repository: "codecrow-public", path: "python-ecosystem/inference-orchestrator/src/service/review/orchestrator/verification_agent.py" },
          { repository: "codecrow-public", path: "java-ecosystem/libs/core/src/main/java/org/rostilos/codecrow/core/service/IssueDeduplicationService.java" },
        ],
      },
    ],
    runtimes,
    snapshotFiles: [
      { id: "program-charter", repository: "workspace", path: "llm-handoff/00-program/CHARTER.md", artifactPath: "program-control/CHARTER.md" },
      { id: "agent-protocol", repository: "workspace", path: "llm-handoff/00-program/AGENT_PROTOCOL.md", artifactPath: "program-control/AGENT_PROTOCOL.md" },
      { id: "task-tracker", repository: "workspace", path: "llm-handoff/00-program/TASK_TRACKER.md", artifactPath: "program-control/TASK_TRACKER.md" },
      { id: "definition-of-done", repository: "workspace", path: "llm-handoff/00-program/DEFINITION_OF_DONE.md", artifactPath: "program-control/DEFINITION_OF_DONE.md" },
      { id: "requirements-traceability", repository: "workspace", path: "llm-handoff/00-program/REQUIREMENTS_TRACEABILITY.md", artifactPath: "program-control/REQUIREMENTS_TRACEABILITY.md" },
      { id: "decision-log", repository: "workspace", path: "llm-handoff/00-program/DECISION_LOG.md", artifactPath: "program-control/DECISION_LOG.md" },
      { id: "phase-zero-specification", repository: "workspace", path: "llm-handoff/03-implementation/phases/00-baseline-and-safety.md", artifactPath: "program-control/00-baseline-and-safety.md" },
      { id: "build-release-gates", repository: "workspace", path: "llm-handoff/04-quality/BUILD_AND_RELEASE_GATES.md", artifactPath: "program-control/BUILD_AND_RELEASE_GATES.md" },
      { id: "known-baseline-failures", repository: "codecrow-public", path: "tools/baseline-manifest/KNOWN_BASELINE_FAILURES.md", artifactPath: "gate-0/KNOWN_BASELINE_FAILURES.md" },
    ],
    workspaceRoot,
  };
}

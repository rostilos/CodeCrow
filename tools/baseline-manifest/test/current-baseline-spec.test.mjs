import assert from "node:assert/strict";
import test from "node:test";

import {
  buildCurrentBaselineSpec,
  probeRuntimeVersions,
} from "../lib/current-baseline-spec.mjs";

test("current baseline spec contains every required offline command and review-affecting prompt", () => {
  const spec = buildCurrentBaselineSpec({
    artifactRoot: "/workspace/codecrow-public/.llm-handoff-artifacts/p0-01",
    capturedAt: "2026-07-14T00:00:00.000Z",
    environment: { platform: "test", release: "1", architecture: "x64", timezone: "UTC" },
    inspectRepository: async () => ({}),
    publicRoot: "/workspace/codecrow-public",
    runtimes: {
      java: "17",
      maven: "3.9",
      python: "3.11",
      node: "24",
      npm: "11",
      container: "Docker 28",
    },
    staticRoot: "/workspace/codecrow-static",
    workspaceRoot: "/workspace",
  });
  const commandIds = new Set(spec.commands.map((command) => command.id));
  for (const id of [
    "baseline-manifest-tests",
    "baseline-manifest-capture",
    "baseline-manifest-verify",
    "frontend-install",
    "frontend-lint",
    "frontend-build",
    "static-install",
    "static-lint",
    "static-build",
    "deployable-release-build",
  ]) {
    assert.ok(commandIds.has(id), id);
  }
  assert.equal(
    spec.commands.find((command) => command.id === "deployable-release-build").authorization,
    "release-ci-only",
  );

  const promptPaths = new Set(spec.promptGroups.flatMap((group) => group.files.map((file) => file.path)));
  for (const fileName of ["constants_shared.py", "constants_branch.py", "constants_mcp.py"]) {
    assert.ok([...promptPaths].some((filePath) => filePath.endsWith(fileName)), fileName);
  }
  const serialized = JSON.stringify(spec);
  assert.ok(!serialized.includes("tools/environment/dumps"));
  assert.ok(!serialized.includes(".env"));
  assert.ok(spec.commands.every((command) => Array.isArray(command.argv) && command.argv.length > 0));
});

test("runtime probes capture stdout/stderr deterministically without ANSI escapes", async () => {
  const calls = [];
  const run = async (command, args) => {
    calls.push([command, args]);
    return command === "java"
      ? { stdout: "", stderr: 'openjdk version "17"\nrest\n' }
      : { stdout: "\u001b[1mversion\u001b[0m\nrest\n", stderr: "" };
  };

  const runtimes = await probeRuntimeVersions(run);

  assert.equal(runtimes.java, 'openjdk version "17"');
  assert.equal(runtimes.maven, "version");
  assert.equal(runtimes.python, "version");
  assert.equal(runtimes.node, "version");
  assert.equal(runtimes.npm, "version");
  assert.equal(runtimes.container, "version");
  assert.deepEqual(calls.map(([command]) => command), ["java", "mvn", "python3", "node", "npm", "docker"]);
});

test("default runtime probes execute the local offline version commands", async () => {
  const runtimes = await probeRuntimeVersions();
  for (const value of Object.values(runtimes)) assert.ok(value.length > 0);
});

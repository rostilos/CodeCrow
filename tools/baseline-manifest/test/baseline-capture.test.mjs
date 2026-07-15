import assert from "node:assert/strict";
import { execFile as execFileCallback } from "node:child_process";
import { mkdir, mkdtemp, readFile, symlink, unlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import test from "node:test";

import { captureBaseline } from "../lib/baseline-capture.mjs";
import { verifyManifestBundle } from "../lib/manifest-validator.mjs";
import { inspectGitRepository } from "../lib/workspace-inspector.mjs";

const execFile = promisify(execFileCallback);

async function git(root, ...args) {
  return execFile("git", ["-C", root, ...args], { encoding: "utf8" });
}

async function repository(root, files) {
  await execFile("git", ["init", "-b", "main", root]);
  await git(root, "config", "user.email", "baseline@example.invalid");
  await git(root, "config", "user.name", "Baseline Test");
  for (const [filePath, contents] of Object.entries(files)) {
    const absolutePath = path.join(root, filePath);
    await mkdir(path.dirname(absolutePath), { recursive: true });
    await writeFile(absolutePath, contents, "utf8");
  }
  await git(root, "add", ".");
  await git(root, "commit", "-m", "fixture");
}

async function captureFixture(mutateOptions = () => {}) {
  const workspaceRoot = await mkdtemp(path.join(os.tmpdir(), "codecrow-capture-"));
  const publicRoot = path.join(workspaceRoot, "codecrow-public");
  const staticRoot = path.join(workspaceRoot, "codecrow-static");
  const artifactRoot = path.join(workspaceRoot, "artifacts");
  await repository(publicRoot, {
    "package-lock.json": "public lock\n",
    "prompt.py": "prompt\n",
    "rule.py": "rule\n",
  });
  await repository(staticRoot, { "package-lock.json": "static lock\n" });
  await writeFile(path.join(workspaceRoot, "fixture.json"), "{}\n", "utf8");
  await writeFile(path.join(workspaceRoot, "program.md"), "# Program\n", "utf8");
  await mkdir(path.join(artifactRoot, "red"), { recursive: true });
  await writeFile(path.join(artifactRoot, "red", "red.txt"), "red evidence\n", "utf8");

  const inspectRepositoryWithSubmodule = async (repositorySpec) => {
    const inspected = await inspectGitRepository(repositorySpec);
    return repositorySpec.id === "codecrow-public"
      ? {
          ...inspected,
          submodules: [
            {
              path: "frontend",
              headCommit: "f".repeat(40),
              dirtyEntries: [],
            },
          ],
        }
      : inspected;
  };
  const options = {
    artifactRoot,
    capturedAt: "2026-07-14T00:00:00.000Z",
    commands: [{ id: "test", workingDirectory: "codecrow-public", argv: ["node", "--test"] }],
    environment: { platform: "test", release: "1", architecture: "x64", timezone: "UTC" },
    existingArtifacts: [{ id: "red", path: "red/red.txt" }],
    fixtures: [{ id: "fixture", repository: "workspace", path: "fixture.json" }],
    index: {
      status: "UNAVAILABLE",
      version: "legacy-unversioned",
      generationId: null,
      reason: "No immutable test index exists.",
    },
    inspectRepository: inspectRepositoryWithSubmodule,
    lockfiles: [
      { repository: "codecrow-public", path: "package-lock.json" },
      { repository: "codecrow-static", path: "package-lock.json" },
    ],
    modelPolicies: [
      {
        purpose: "review",
        status: "UNCONFIGURED",
        providerId: null,
        modelId: null,
        reason: "Offline test capture.",
      },
    ],
    promptGroups: [
      { id: "prompts", files: [{ repository: "codecrow-public", path: "prompt.py" }] },
    ],
    randomSeeds: [{ id: "test", value: 42 }],
    repositories: [
      { id: "codecrow-public", root: publicRoot },
      { id: "codecrow-static", root: staticRoot },
    ],
    ruleGroups: [
      { id: "rules", files: [{ repository: "codecrow-public", path: "rule.py" }] },
    ],
    runtimes: {
      java: "17",
      maven: "3.9",
      python: "3.11",
      node: "24",
      npm: "11",
      container: "Docker 28",
    },
    snapshotFiles: [
      {
        id: "program",
        repository: "workspace",
        path: "program.md",
        artifactPath: "program-control/program.md",
      },
    ],
    workspaceRoot,
  };
  await mutateOptions(options, { artifactRoot, publicRoot, staticRoot, workspaceRoot });
  const result = await captureBaseline(options);

  return { artifactRoot, options, publicRoot, result, workspaceRoot };
}

test("captures and independently verifies a complete immutable baseline bundle", async () => {
  const { artifactRoot, result } = await captureFixture();
  const manifestPath = path.join(artifactRoot, "baseline-manifest.json");
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));

  assert.equal(result.manifestPath, manifestPath);
  assert.equal(manifest.repositories.length, 2);
  assert.equal(manifest.repositories[0].dirtyState.captured, true);
  assert.deepEqual(manifest.repositories[0].dirtyState.entries, []);
  assert.equal(manifest.repositories[0].submodules[0].path, "frontend");
  assert.match(manifest.configuration.prompts[0].version, /^sha256:[a-f0-9]{64}$/);
  assert.match(manifest.workspace.environmentFingerprintSha256, /^[a-f0-9]{64}$/);
  assert.ok(manifest.artifacts.some((artifact) => artifact.path === "program-control/program.md"));

  const verified = await verifyManifestBundle(manifestPath, {
    inspectRepository: (repositorySpec) =>
      repositorySpec.id === "codecrow-public"
        ? inspectGitRepository(repositorySpec).then((inspected) => ({
            ...inspected,
            submodules: [
              { path: "frontend", headCommit: "f".repeat(40), dirtyEntries: [] },
            ],
          }))
        : inspectGitRepository(repositorySpec),
    verifyWorkspace: true,
  });
  assert.equal(verified.valid, true, verified.errors.join("\n"));
});

test("captured bundle detects later artifact and source-input changes", async () => {
  const { artifactRoot, options } = await captureFixture();
  const manifestPath = path.join(artifactRoot, "baseline-manifest.json");
  await writeFile(path.join(artifactRoot, "program-control", "program.md"), "tampered\n", "utf8");

  const artifactVerification = await verifyManifestBundle(manifestPath, {
    inspectRepository: options.inspectRepository,
    verifyWorkspace: true,
  });

  assert.equal(artifactVerification.valid, false);
  assert.ok(artifactVerification.errors.some((error) => error.includes("artifacts")));

  const sourceFixture = await captureFixture();
  await writeFile(path.join(sourceFixture.publicRoot, "prompt.py"), "changed\n", "utf8");
  const sourceVerification = await verifyManifestBundle(
    path.join(sourceFixture.artifactRoot, "baseline-manifest.json"),
    {
      inspectRepository: sourceFixture.options.inspectRepository,
      verifyWorkspace: true,
    },
  );

  assert.equal(sourceVerification.valid, false);
  assert.ok(sourceVerification.errors.some((error) => error.includes("configuration.prompts")));
  assert.ok(sourceVerification.errors.some((error) => error.includes("dirtyState.entries mismatch")));
});

test("capture fails closed on path traversal and unknown source roots", async () => {
  await assert.rejects(
    () => captureFixture((options) => (options.existingArtifacts[0].path = "../outside")),
    /must stay within/,
  );
  await assert.rejects(
    () => captureFixture((options) => (options.lockfiles[0].repository = "unknown")),
    /unknown source root/,
  );
  await assert.rejects(
    () => captureFixture((options) => (options.snapshotFiles[0].repository = "unknown")),
    /unknown source root/,
  );
});

test("capture refuses to emit a bundle when the workspace changes during capture", async () => {
  await assert.rejects(
    () =>
      captureFixture((options) => {
        const inspect = options.inspectRepository;
        let calls = 0;
        options.inspectRepository = async (repositorySpec) => {
          const inspected = await inspect(repositorySpec);
          calls += 1;
          return calls > options.repositories.length
            ? { ...inspected, headCommit: "e".repeat(40) }
            : inspected;
        };
      }),
    /Captured baseline failed validation/,
  );
});

test("capture rejects source, snapshot-destination, and retained-artifact symlink escapes", async () => {
  await assert.rejects(
    () =>
      captureFixture(async (options, { workspaceRoot }) => {
        const outsideRoot = await mkdtemp(path.join(os.tmpdir(), "codecrow-capture-source-outside-"));
        const outsideFile = path.join(outsideRoot, "secret.txt");
        await writeFile(outsideFile, "secret\n", "utf8");
        await unlink(path.join(workspaceRoot, "fixture.json"));
        await symlink(outsideFile, path.join(workspaceRoot, "fixture.json"));
      }),
    /symbolic link|source root/,
  );

  await assert.rejects(
    () =>
      captureFixture(async (options, { artifactRoot }) => {
        const outsideRoot = await mkdtemp(path.join(os.tmpdir(), "codecrow-capture-snapshot-outside-"));
        await symlink(outsideRoot, path.join(artifactRoot, "program-control"));
      }),
    /symbolic link|artifact root|declared root/,
  );

  await assert.rejects(
    () =>
      captureFixture(async (options, { artifactRoot }) => {
        const outsideRoot = await mkdtemp(path.join(os.tmpdir(), "codecrow-capture-final-link-outside-"));
        const outsideFile = path.join(outsideRoot, "secret.txt");
        await writeFile(outsideFile, "secret\n", "utf8");
        await mkdir(path.join(artifactRoot, "program-control"), { recursive: true });
        await symlink(outsideFile, path.join(artifactRoot, "program-control", "program.md"));
      }),
    /symbolic link/,
  );

  await assert.rejects(
    () =>
      captureFixture(async (options, { artifactRoot }) => {
        const outsideRoot = await mkdtemp(path.join(os.tmpdir(), "codecrow-capture-artifact-outside-"));
        const outsideFile = path.join(outsideRoot, "secret.txt");
        await writeFile(outsideFile, "secret\n", "utf8");
        await unlink(path.join(artifactRoot, "red", "red.txt"));
        await symlink(outsideFile, path.join(artifactRoot, "red", "red.txt"));
      }),
    /symbolic link|artifact root|declared root/,
  );
});

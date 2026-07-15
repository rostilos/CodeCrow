import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, symlink, unlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  canonicalJson,
  sha256Bytes,
  validateManifest,
  verifyManifestBundle,
  writeManifestBundle,
} from "../lib/manifest-validator.mjs";

const SHA_A = "a".repeat(40);
const SHA_B = "b".repeat(40);

async function fixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), "codecrow-baseline-"));
  const publicRoot = path.join(root, "codecrow-public");
  const staticRoot = path.join(root, "codecrow-static");
  await mkdir(publicRoot, { recursive: true });
  await mkdir(staticRoot, { recursive: true });
  const artifactPath = path.join(root, "gate-0.txt");
  await writeFile(artifactPath, "offline evidence\n", "utf8");
  await writeFile(path.join(staticRoot, "package-lock.json"), "lock\n", "utf8");
  await writeFile(path.join(publicRoot, "prompt_constants.py"), "prompt\n", "utf8");
  await writeFile(path.join(publicRoot, "rules.java"), "rules\n", "utf8");
  await writeFile(path.join(root, "manifest-fixture.txt"), "fixture\n", "utf8");
  const runtimes = {
    java: "17.0.17",
    maven: "3.9.9",
    python: "3.11.11",
    node: "24.6.0",
    npm: "11.5.1",
    container: "Docker 28.3.3",
  };
  const environmentContents = canonicalJson({
    environment: { platform: "test", release: "1", architecture: "x64", timezone: "UTC" },
    runtimes,
  });
  await writeFile(path.join(root, "environment.json"), environmentContents, "utf8");

  const manifest = {
    schemaVersion: "codecrow.baseline-manifest/v1",
    capturedAt: "2026-07-14T00:00:00.000Z",
    workspace: {
      root,
      environmentFingerprintSha256: sha256Bytes(environmentContents),
    },
    repositories: [
      {
        id: "codecrow-public",
        root: publicRoot,
        branch: "2.0.0-rc",
        headCommit: SHA_A,
        dirtyState: {
          captured: true,
          entries: [
            {
              status: " M",
              path: "deployment/build/production-build.sh",
              contentSha256: "2".repeat(64),
            },
          ],
        },
        submodules: [
          {
            path: "frontend",
            headCommit: SHA_B,
            dirtyState: { captured: true, entries: [] },
          },
        ],
      },
      {
        id: "codecrow-static",
        root: staticRoot,
        branch: "2.0.0-rc",
        headCommit: SHA_B,
        dirtyState: { captured: true, entries: [] },
        submodules: [],
      },
    ],
    runtimes,
    lockfiles: [
      {
        repository: "codecrow-static",
        path: "package-lock.json",
        sha256: sha256Bytes("lock\n"),
      },
    ],
    commands: [
      {
        id: "static-build",
        workingDirectory: "codecrow-static",
        argv: ["npm", "run", "build"],
      },
    ],
    configuration: {
      modelPolicies: [
        {
          purpose: "review",
          status: "UNCONFIGURED",
          providerId: null,
          modelId: null,
          reason: "No provider credentials or runtime overrides are loaded for offline baseline capture.",
        },
      ],
      prompts: [
        {
          id: "review-prompts",
          version: "sha256:4",
          files: [
            {
              repository: "codecrow-public",
              path: "prompt_constants.py",
              sha256: sha256Bytes("prompt\n"),
            },
          ],
        },
      ],
      rules: [
        {
          id: "review-rules",
          version: "sha256:5",
          files: [
            {
              repository: "codecrow-public",
              path: "rules.java",
              sha256: sha256Bytes("rules\n"),
            },
          ],
        },
      ],
      index: {
        status: "UNAVAILABLE",
        version: "legacy-unversioned",
        generationId: null,
        reason: "The legacy system has no immutable local index-generation manifest.",
      },
    },
    fixtures: [
      {
        id: "manifest-tests",
        repository: "workspace",
        path: "manifest-fixture.txt",
        sha256: sha256Bytes("fixture\n"),
      },
    ],
    randomSeeds: [{ id: "baseline", value: 424242 }],
    artifacts: [
      {
        id: "gate-0",
        path: "gate-0.txt",
        sha256: sha256Bytes("offline evidence\n"),
      },
      {
        id: "environment",
        path: "environment.json",
        sha256: sha256Bytes(environmentContents),
      },
    ],
  };
  for (const group of [manifest.configuration.prompts[0], manifest.configuration.rules[0]]) {
    group.version = `sha256:${sha256Bytes(canonicalJson(group.files))}`;
  }

  return { root, artifactPath, manifest };
}

function clone(value) {
  return structuredClone(value);
}

function matchingInspector(manifest) {
  return async ({ id }) => {
    const repository = manifest.repositories.find((candidate) => candidate.id === id);
    return {
      headCommit: repository.headCommit,
      branch: repository.branch,
      dirtyEntries: repository.dirtyState.entries,
      submodules: repository.submodules.map((submodule) => ({
        path: submodule.path,
        headCommit: submodule.headCommit,
        dirtyEntries: submodule.dirtyState.entries,
      })),
    };
  };
}

function removePath(value, dottedPath) {
  const copy = clone(value);
  const parts = dottedPath.split(".");
  const key = parts.pop();
  let cursor = copy;
  for (const part of parts) {
    cursor = /^\d+$/.test(part) ? cursor[Number(part)] : cursor[part];
  }
  delete cursor[key];
  return copy;
}

test("accepts a complete reproducibility manifest and verifies its artifact", async () => {
  const { manifest, root } = await fixture();
  const result = await validateManifest(manifest, { manifestDirectory: root });

  assert.equal(result.valid, true, result.errors.join("\n"));
  assert.deepEqual(result.errors, []);
  assert.match(result.manifestSha256, /^[a-f0-9]{64}$/);
});

for (const requiredPath of [
  "repositories.0.headCommit",
  "repositories.0.dirtyState",
  "configuration.modelPolicies",
  "configuration.prompts.0.version",
  "configuration.rules.0.version",
  "configuration.index.version",
  "commands",
  "randomSeeds",
  "workspace.environmentFingerprintSha256",
  "artifacts.0.sha256",
]) {
  test(`rejects a missing required reproducibility field: ${requiredPath}`, async () => {
    const { manifest, root } = await fixture();
    const result = await validateManifest(removePath(manifest, requiredPath), {
      manifestDirectory: root,
    });

    assert.equal(result.valid, false);
    assert.ok(result.errors.some((error) => error.includes(requiredPath)));
  });
}

test("rejects an altered referenced artifact", async () => {
  const { artifactPath, manifest, root } = await fixture();
  await writeFile(artifactPath, "tampered evidence\n", "utf8");

  const result = await validateManifest(manifest, { manifestDirectory: root });

  assert.equal(result.valid, false);
  assert.ok(result.errors.some((error) => error.includes("artifacts.0.sha256 mismatch")));
});

test("rejects an artifact path that escapes the manifest directory", async () => {
  const { manifest, root } = await fixture();
  manifest.artifacts[0].path = "../outside.txt";

  const result = await validateManifest(manifest, { manifestDirectory: root });

  assert.equal(result.valid, false);
  assert.ok(result.errors.some((error) => error.includes("must stay within")));
});

test("workspace verification detects a mixed revision and incomplete dirty inventory", async () => {
  const { manifest, root } = await fixture();
  const inspectRepository = async ({ id }) =>
    id === "codecrow-public"
      ? {
          headCommit: "c".repeat(40),
          branch: "2.0.0-rc",
          dirtyEntries: [],
          submodules: [
            { path: "frontend", headCommit: SHA_B, dirtyEntries: [] },
          ],
        }
      : { headCommit: SHA_B, branch: "2.0.0-rc", dirtyEntries: [], submodules: [] };

  const result = await validateManifest(manifest, {
    inspectRepository,
    manifestDirectory: root,
    verifyWorkspace: true,
  });

  assert.equal(result.valid, false);
  assert.ok(result.errors.some((error) => error.includes("headCommit mismatch")));
  assert.ok(result.errors.some((error) => error.includes("dirtyState.entries mismatch")));
});

test("workspace verification detects a changed dirty-file digest and submodule revision", async () => {
  const { manifest, root } = await fixture();
  const inspectRepository = async ({ id }) =>
    id === "codecrow-public"
      ? {
          headCommit: SHA_A,
          branch: "other-branch",
          dirtyEntries: [
            {
              status: " M",
              path: "deployment/build/production-build.sh",
              contentSha256: "9".repeat(64),
            },
          ],
          submodules: [
            { path: "frontend", headCommit: "d".repeat(40), dirtyEntries: [] },
          ],
        }
      : { headCommit: SHA_B, branch: "2.0.0-rc", dirtyEntries: [], submodules: [] };

  const result = await validateManifest(manifest, {
    inspectRepository,
    manifestDirectory: root,
    verifyWorkspace: true,
  });

  assert.equal(result.valid, false);
  assert.ok(result.errors.some((error) => error.includes("branch mismatch")));
  assert.ok(result.errors.some((error) => error.includes("dirtyState.entries mismatch")));
  assert.ok(result.errors.some((error) => error.includes("submodules.frontend.headCommit mismatch")));
});

test("workspace verification rejects a changed lock, prompt, rule, or fixture input", async () => {
  const { manifest, root } = await fixture();
  await writeFile(path.join(root, "codecrow-public", "prompt_constants.py"), "changed prompt\n", "utf8");

  const result = await validateManifest(manifest, {
    inspectRepository: matchingInspector(manifest),
    manifestDirectory: root,
    verifyWorkspace: true,
  });

  assert.equal(result.valid, false);
  assert.ok(result.errors.some((error) => error.includes("configuration.prompts.0.files.0.sha256 mismatch")));
});

test("workspace verification reports malformed source collections without throwing", async () => {
  const { manifest, root } = await fixture();
  manifest.configuration.prompts = null;

  const result = await validateManifest(manifest, {
    inspectRepository: matchingInspector(manifest),
    manifestDirectory: root,
    verifyWorkspace: true,
  });

  assert.equal(result.valid, false);
  assert.ok(result.errors.some((error) => error.includes("configuration.prompts")));
});

test("rejects false prompt/rule versions and environment fingerprints", async () => {
  const { manifest, root } = await fixture();
  manifest.configuration.prompts[0].version = `sha256:${"9".repeat(64)}`;
  manifest.configuration.rules[0].version = `sha256:${"8".repeat(64)}`;
  manifest.workspace.environmentFingerprintSha256 = "7".repeat(64);

  const result = await validateManifest(manifest, { manifestDirectory: root });

  assert.equal(result.valid, false);
  assert.ok(result.errors.some((error) => error.includes("configuration.prompts.0.version mismatch")));
  assert.ok(result.errors.some((error) => error.includes("configuration.rules.0.version mismatch")));
  assert.ok(result.errors.some((error) => error.includes("environmentFingerprintSha256 mismatch")));
});

test("rejects an environment artifact whose runtime identity disagrees with the manifest", async () => {
  const { manifest, root } = await fixture();
  const changedEnvironment = canonicalJson({
    environment: { platform: "test", release: "1", architecture: "x64", timezone: "UTC" },
    runtimes: { ...manifest.runtimes, node: "different" },
  });
  await writeFile(path.join(root, "environment.json"), changedEnvironment, "utf8");
  manifest.artifacts.find((artifact) => artifact.id === "environment").sha256 = sha256Bytes(changedEnvironment);
  manifest.workspace.environmentFingerprintSha256 = sha256Bytes(changedEnvironment);

  const result = await validateManifest(manifest, { manifestDirectory: root });

  assert.equal(result.valid, false);
  assert.ok(result.errors.some((error) => error.includes("environment runtimes mismatch")));
});

test("rejects source and artifact symlinks that escape their declared roots", async () => {
  const { manifest, root } = await fixture();
  const outsideRoot = await mkdtemp(path.join(os.tmpdir(), "codecrow-outside-"));
  const outsidePrompt = path.join(outsideRoot, "secret-prompt.txt");
  const outsideArtifact = path.join(outsideRoot, "secret-artifact.txt");
  await writeFile(outsidePrompt, "outside prompt\n", "utf8");
  await writeFile(outsideArtifact, "outside artifact\n", "utf8");
  await unlink(path.join(root, "codecrow-public", "prompt_constants.py"));
  await symlink(outsidePrompt, path.join(root, "codecrow-public", "prompt_constants.py"));
  await symlink(outsideArtifact, path.join(root, "escaped-artifact.txt"));
  const promptFile = manifest.configuration.prompts[0].files[0];
  promptFile.sha256 = sha256Bytes("outside prompt\n");
  manifest.configuration.prompts[0].version = `sha256:${sha256Bytes(canonicalJson(manifest.configuration.prompts[0].files))}`;
  manifest.artifacts[0] = {
    id: "gate-0",
    path: "escaped-artifact.txt",
    sha256: sha256Bytes("outside artifact\n"),
  };

  const result = await validateManifest(manifest, {
    inspectRepository: matchingInspector(manifest),
    manifestDirectory: root,
    verifyWorkspace: true,
  });

  assert.equal(result.valid, false);
  assert.ok(result.errors.some((error) => error.includes("symbolic link")));
});

for (const [label, mutate, expected] of [
  [
    "unknown source root",
    (manifest) => (manifest.fixtures[0].repository = "unknown"),
    "repository must name workspace or a manifest repository",
  ],
  [
    "source-root traversal",
    (manifest) => (manifest.fixtures[0].path = "../outside"),
    "path must stay within its declared root",
  ],
  [
    "missing source input",
    (manifest) => (manifest.fixtures[0].path = "absent.txt"),
    "path cannot be read",
  ],
]) {
  test(`workspace verification rejects ${label}`, async () => {
    const { manifest, root } = await fixture();
    mutate(manifest);
    const result = await validateManifest(manifest, {
      inspectRepository: matchingInspector(manifest),
      manifestDirectory: root,
      verifyWorkspace: true,
    });
    assert.equal(result.valid, false);
    assert.ok(result.errors.some((error) => error.includes(expected)));
  });
}

test("writes and verifies a detached manifest checksum", async () => {
  const { manifest, root } = await fixture();
  const manifestPath = path.join(root, "baseline-manifest.json");

  const written = await writeManifestBundle(manifestPath, manifest);
  const verified = await verifyManifestBundle(manifestPath);

  assert.equal(verified.valid, true, verified.errors.join("\n"));
  assert.equal(verified.manifestSha256, written.manifestSha256);
  assert.equal(
    (await readFile(`${manifestPath}.sha256`, "utf8")).trim(),
    `${written.manifestSha256}  baseline-manifest.json`,
  );

  await writeFile(manifestPath, `${await readFile(manifestPath, "utf8")} `, "utf8");
  const tampered = await verifyManifestBundle(manifestPath);
  assert.equal(tampered.valid, false);
  assert.ok(tampered.errors.some((error) => error.includes("detached checksum mismatch")));
});

test("canonical JSON is stable across object insertion order", () => {
  assert.equal(
    canonicalJson({ z: 1, a: { y: 2, b: 3 } }),
    canonicalJson({ a: { b: 3, y: 2 }, z: 1 }),
  );
});

test("rejects malformed, duplicate, and internally inconsistent records without throwing", async () => {
  const { manifest, root } = await fixture();
  manifest.schemaVersion = "unknown";
  manifest.capturedAt = "not-a-date";
  manifest.workspace.root = "";
  manifest.repositories[0].dirtyState.captured = false;
  delete manifest.repositories[0].dirtyState.entries[0].contentSha256;
  manifest.repositories[0].dirtyState.entries.push(null);
  manifest.repositories.push(clone(manifest.repositories[0]));
  manifest.repositories[2].submodules.push(clone(manifest.repositories[2].submodules[0]));
  manifest.lockfiles[0].repository = "missing-repository";
  manifest.commands.push(clone(manifest.commands[0]));
  manifest.commands[1].argv.push("");
  manifest.configuration.modelPolicies[0] = {
    purpose: "review",
    status: "CONFIGURED",
    providerId: "",
    modelId: "",
  };
  delete manifest.configuration.index.generationId;
  manifest.randomSeeds[0].value = 1.5;

  const result = await validateManifest(manifest, { manifestDirectory: root });

  assert.equal(result.valid, false);
  for (const expected of [
    "schemaVersion",
    "capturedAt",
    "workspace.root",
    "dirtyState.captured",
    "contentSha256 must be explicit",
    "id must be unique",
    "path must be unique",
    "must name a manifest repository",
    "providerId",
    "modelId",
    "generationId must be explicit",
    "value must be a safe integer",
  ]) {
    assert.ok(result.errors.some((error) => error.includes(expected)), expected);
  }
});

test("accepts explicit configured model and ready index identities", async () => {
  const { manifest, root } = await fixture();
  manifest.configuration.modelPolicies[0] = {
    purpose: "review",
    status: "CONFIGURED",
    providerId: "fake-provider",
    modelId: "fake-model-v1",
  };
  manifest.configuration.index = {
    status: "READY",
    version: "index-contract-v1",
    generationId: "generation-1",
  };

  const result = await validateManifest(manifest, { manifestDirectory: root });

  assert.equal(result.valid, true, result.errors.join("\n"));
});

test("rejects a ready index without a generation identity", async () => {
  const { manifest, root } = await fixture();
  manifest.configuration.index = {
    status: "READY",
    version: "index-contract-v1",
    generationId: "",
  };

  const result = await validateManifest(manifest, { manifestDirectory: root });

  assert.equal(result.valid, false);
  assert.ok(result.errors.some((error) => error.includes("generationId")));
});

test("rejects a missing artifact and covers deterministic dirty-entry ordering", async () => {
  const { manifest, root } = await fixture();
  manifest.artifacts[0].path = "missing.txt";
  manifest.repositories[0].dirtyState.entries.push({
    status: "??",
    path: "new-file",
    contentSha256: null,
  });
  const inspectRepository = async ({ id }) =>
    id === "codecrow-public"
      ? {
          headCommit: SHA_A,
          branch: "2.0.0-rc",
          dirtyEntries: [...manifest.repositories[0].dirtyState.entries].reverse(),
          submodules: [{ path: "frontend", headCommit: SHA_B, dirtyEntries: [] }],
        }
      : { headCommit: SHA_B, branch: "2.0.0-rc", dirtyEntries: [], submodules: [] };

  const result = await validateManifest(manifest, {
    inspectRepository,
    manifestDirectory: root,
    verifyWorkspace: true,
  });

  assert.equal(result.valid, false);
  assert.ok(result.errors.some((error) => error.includes("path cannot be read")));
  assert.ok(!result.errors.some((error) => error.includes("dirtyState.entries mismatch")));
});

test("workspace verification reports inspection, missing-submodule, and extra-submodule failures", async () => {
  const { manifest, root } = await fixture();
  const inspectRepository = async ({ id }) => {
    if (id === "codecrow-static") throw new Error("simulated git failure");
    return {
      headCommit: SHA_A,
      branch: "2.0.0-rc",
      dirtyEntries: manifest.repositories[0].dirtyState.entries,
      submodules: [
        { path: "unexpected", headCommit: SHA_B, dirtyEntries: [] },
        { path: "also-unexpected", headCommit: SHA_B, dirtyEntries: [] },
      ],
    };
  };

  const result = await validateManifest(manifest, {
    inspectRepository,
    manifestDirectory: root,
    verifyWorkspace: true,
  });

  assert.equal(result.valid, false);
  assert.ok(result.errors.some((error) => error.includes("cannot be inspected")));
  assert.ok(result.errors.some((error) => error.includes("submodules.frontend is missing")));
  assert.ok(result.errors.some((error) => error.includes("submodules inventory mismatch")));
});

test("workspace verification requires an inspector", async () => {
  const { manifest, root } = await fixture();
  const result = await validateManifest(manifest, {
    manifestDirectory: root,
    verifyWorkspace: true,
  });

  assert.equal(result.valid, false);
  assert.ok(result.errors.includes("workspace verification requires inspectRepository"));
});

test("rejects a non-object manifest", async () => {
  const result = await validateManifest(null);
  assert.equal(result.valid, false);
  assert.ok(result.errors.includes("manifest must be an object"));
});

test("bundle verification reports missing manifest, checksum, and invalid JSON", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "codecrow-baseline-errors-"));
  const missing = await verifyManifestBundle(path.join(root, "missing.json"));
  assert.equal(missing.valid, false);
  assert.ok(missing.errors.some((error) => error.includes("manifest cannot be read")));

  const invalidPath = path.join(root, "invalid.json");
  await writeFile(invalidPath, "{", "utf8");
  const invalid = await verifyManifestBundle(invalidPath);
  assert.equal(invalid.valid, false);
  assert.ok(invalid.errors.some((error) => error.includes("detached checksum cannot be read")));
  assert.ok(invalid.errors.some((error) => error.includes("manifest JSON is invalid")));
});

test("environment identity reports an unreadable or malformed artifact", async () => {
  const { manifest, root } = await fixture();
  await writeFile(path.join(root, "environment.json"), "{", "utf8");
  manifest.artifacts.find((artifact) => artifact.id === "environment").sha256 = sha256Bytes("{");
  manifest.workspace.environmentFingerprintSha256 = sha256Bytes("{");

  const result = await validateManifest(manifest, { manifestDirectory: root });

  assert.equal(result.valid, false);
  assert.ok(result.errors.some((error) => error.includes("environment artifact cannot be validated")));
});

test("workspace dirty-state comparison is order independent for multiple entries", async () => {
  const { manifest, root } = await fixture();
  manifest.repositories[0].dirtyState.entries.push({
    status: "??",
    path: "another-file",
    contentSha256: null,
  });
  const inspectRepository = matchingInspector(manifest);
  const original = await inspectRepository({ id: "codecrow-public" });
  const result = await validateManifest(manifest, {
    inspectRepository: async (repository) =>
      repository.id === "codecrow-public"
        ? { ...original, dirtyEntries: [...original.dirtyEntries].reverse() }
        : matchingInspector(manifest)(repository),
    manifestDirectory: root,
    verifyWorkspace: true,
  });

  assert.equal(result.valid, true, result.errors.join("\n"));
});

const malformedShapeCases = [
  ["repository collection", (manifest) => (manifest.repositories = {})],
  ["repository record", (manifest) => (manifest.repositories = [null])],
  ["dirty entry collection", (manifest) => (manifest.repositories[0].dirtyState.entries = null)],
  ["submodule collection", (manifest) => (manifest.repositories[0].submodules = null)],
  ["submodule record", (manifest) => (manifest.repositories[0].submodules = [null])],
  ["runtime record", (manifest) => (manifest.runtimes = null)],
  ["lockfile collection", (manifest) => (manifest.lockfiles = null)],
  ["lockfile record", (manifest) => (manifest.lockfiles = [null])],
  ["command record", (manifest) => (manifest.commands = [null])],
  ["configuration record", (manifest) => (manifest.configuration = null)],
  ["model-policy record", (manifest) => (manifest.configuration.modelPolicies = [null])],
  ["model-policy status", (manifest) => (manifest.configuration.modelPolicies[0].status = "")],
  [
    "unavailable model identity",
    (manifest) => {
      delete manifest.configuration.modelPolicies[0].providerId;
      delete manifest.configuration.modelPolicies[0].modelId;
    },
  ],
  ["prompt collection", (manifest) => (manifest.configuration.prompts = null)],
  ["prompt group", (manifest) => (manifest.configuration.prompts = [null])],
  ["prompt file collection", (manifest) => (manifest.configuration.prompts[0].files = null)],
  ["prompt file record", (manifest) => (manifest.configuration.prompts[0].files = [null])],
  ["fixture collection", (manifest) => (manifest.fixtures = null)],
  ["fixture record", (manifest) => (manifest.fixtures = [null])],
  ["seed record", (manifest) => (manifest.randomSeeds = [null])],
  ["artifact collection", (manifest) => (manifest.artifacts = null)],
  ["artifact record", (manifest) => (manifest.artifacts = [null])],
  ["artifact path", (manifest) => (manifest.artifacts[0].path = "")],
  ["duplicate artifact id", (manifest) => manifest.artifacts.push(clone(manifest.artifacts[0]))],
  [
    "environment artifact path type",
    (manifest) => (manifest.artifacts.find((artifact) => artifact.id === "environment").path = null),
  ],
];

for (const [label, mutate] of malformedShapeCases) {
  test(`rejects malformed ${label}`, async () => {
    const { manifest, root } = await fixture();
    mutate(manifest);
    const result = await validateManifest(manifest, { manifestDirectory: root });
    assert.equal(result.valid, false);
    assert.ok(result.errors.length > 0);
  });
}

import { chmod, copyFile, mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

import {
  canonicalJson,
  sha256Bytes,
  verifyManifestBundle,
  writeManifestBundle,
} from "./manifest-validator.mjs";
import { safeInputPath, safeOutputPath } from "./safe-path.mjs";

async function sourceRecord(record, sourceRoots, label) {
  const root = sourceRoots.get(record.repository);
  if (root === undefined) throw new Error(`${label} has unknown source root ${record.repository}`);
  const absolutePath = await safeInputPath(root, record.path, label);
  return { ...record, sha256: sha256Bytes(await readFile(absolutePath)) };
}

async function versionedGroup(group, sourceRoots, label) {
  const files = await Promise.all(
    group.files.map((file, index) => sourceRecord(file, sourceRoots, `${label}.files.${index}`)),
  );
  return {
    id: group.id,
    version: `sha256:${sha256Bytes(canonicalJson(files))}`,
    files,
  };
}

async function writeJsonArtifact(artifactRoot, id, artifactPath, value) {
  const absolutePath = await safeOutputPath(artifactRoot, artifactPath, `artifact ${id}`);
  await writeFile(absolutePath, canonicalJson(value), { encoding: "utf8", mode: 0o600 });
  return { id, path: artifactPath, sha256: sha256Bytes(await readFile(absolutePath)) };
}

async function snapshotArtifact(snapshot, sourceRoots, artifactRoot) {
  const sourceRoot = sourceRoots.get(snapshot.repository);
  if (sourceRoot === undefined) throw new Error(`snapshot ${snapshot.id} has unknown source root ${snapshot.repository}`);
  const sourcePath = await safeInputPath(sourceRoot, snapshot.path, `snapshot source ${snapshot.id}`);
  const artifactPath = await safeOutputPath(artifactRoot, snapshot.artifactPath, `snapshot artifact ${snapshot.id}`);
  await copyFile(sourcePath, artifactPath);
  await chmod(artifactPath, 0o600);
  return {
    id: snapshot.id,
    path: snapshot.artifactPath,
    sha256: sha256Bytes(await readFile(artifactPath)),
  };
}

async function existingArtifact(artifact, artifactRoot) {
  const absolutePath = await safeInputPath(artifactRoot, artifact.path, `existing artifact ${artifact.id}`);
  return { ...artifact, sha256: sha256Bytes(await readFile(absolutePath)) };
}

function repositoryRecord(repository, inspected) {
  return {
    id: repository.id,
    root: path.resolve(repository.root),
    branch: inspected.branch,
    headCommit: inspected.headCommit,
    dirtyState: { captured: true, entries: inspected.dirtyEntries },
    submodules: inspected.submodules.map((submodule) => ({
      path: submodule.path,
      headCommit: submodule.headCommit,
      dirtyState: { captured: true, entries: submodule.dirtyEntries },
    })),
  };
}

export async function captureBaseline(options) {
  const {
    artifactRoot,
    capturedAt,
    commands,
    environment,
    existingArtifacts,
    fixtures,
    index,
    inspectRepository,
    lockfiles,
    modelPolicies,
    promptGroups,
    randomSeeds,
    repositories,
    ruleGroups,
    runtimes,
    snapshotFiles,
    workspaceRoot,
  } = options;

  await mkdir(artifactRoot, { recursive: true, mode: 0o700 });
  const inspectedRepositories = await Promise.all(
    repositories.map(async (repository) =>
      repositoryRecord(repository, await inspectRepository(repository)),
    ),
  );
  const sourceRoots = new Map([
    ["workspace", path.resolve(workspaceRoot)],
    ...inspectedRepositories.map((repository) => [repository.id, repository.root]),
  ]);
  const [resolvedLockfiles, resolvedPrompts, resolvedRules, resolvedFixtures] = await Promise.all([
    Promise.all(lockfiles.map((record, index) => sourceRecord(record, sourceRoots, `lockfiles.${index}`))),
    Promise.all(promptGroups.map((group, index) => versionedGroup(group, sourceRoots, `prompts.${index}`))),
    Promise.all(ruleGroups.map((group, index) => versionedGroup(group, sourceRoots, `rules.${index}`))),
    Promise.all(fixtures.map((record, index) => sourceRecord(record, sourceRoots, `fixtures.${index}`))),
  ]);

  const environmentRecord = { environment, runtimes };
  const generatedArtifacts = await Promise.all([
    writeJsonArtifact(artifactRoot, "repository-state", "gate-0/repository-state.json", inspectedRepositories),
    writeJsonArtifact(artifactRoot, "environment", "gate-0/environment.json", environmentRecord),
    writeJsonArtifact(artifactRoot, "command-inventory", "gate-0/command-inventory.json", commands),
  ]);
  const [snapshots, retainedArtifacts] = await Promise.all([
    Promise.all(snapshotFiles.map((snapshot) => snapshotArtifact(snapshot, sourceRoots, artifactRoot))),
    Promise.all(existingArtifacts.map((artifact) => existingArtifact(artifact, artifactRoot))),
  ]);
  const artifacts = [...generatedArtifacts, ...snapshots, ...retainedArtifacts].sort((left, right) =>
    left.path.localeCompare(right.path),
  );

  const manifest = {
    schemaVersion: "codecrow.baseline-manifest/v1",
    capturedAt,
    workspace: {
      root: path.resolve(workspaceRoot),
      environmentFingerprintSha256: sha256Bytes(canonicalJson(environmentRecord)),
    },
    repositories: inspectedRepositories,
    runtimes,
    lockfiles: resolvedLockfiles,
    commands,
    configuration: {
      modelPolicies,
      prompts: resolvedPrompts,
      rules: resolvedRules,
      index,
    },
    fixtures: resolvedFixtures,
    randomSeeds,
    artifacts,
  };
  const manifestPath = path.join(path.resolve(artifactRoot), "baseline-manifest.json");
  const written = await writeManifestBundle(manifestPath, manifest);
  const validation = await verifyManifestBundle(manifestPath, {
    inspectRepository,
    verifyWorkspace: true,
  });
  if (!validation.valid) {
    throw new Error(`Captured baseline failed validation:\n${validation.errors.join("\n")}`);
  }
  return { manifestPath, manifestSha256: written.manifestSha256 };
}

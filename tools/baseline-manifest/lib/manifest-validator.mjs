import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";

import { safeInputPath } from "./safe-path.mjs";

const SCHEMA_VERSION = "codecrow.baseline-manifest/v1";
const SHA256 = /^[a-f0-9]{64}$/;
const GIT_COMMIT = /^[a-f0-9]{40}(?:[a-f0-9]{24})?$/;

function normalize(value) {
  if (Array.isArray(value)) {
    return value.map(normalize);
  }
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, normalize(value[key])]),
    );
  }
  return value;
}

export function canonicalJson(value) {
  return `${JSON.stringify(normalize(value), null, 2)}\n`;
}

export function sha256Bytes(value) {
  return createHash("sha256").update(value).digest("hex");
}

function hasOwn(value, key) {
  return value !== null && typeof value === "object" && Object.hasOwn(value, key);
}

function requireObject(value, field, errors) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    errors.push(`${field} must be an object`);
    return false;
  }
  return true;
}

function requireString(value, field, errors) {
  if (typeof value !== "string" || value.trim() === "") {
    errors.push(`${field} must be a non-empty string`);
    return false;
  }
  return true;
}

function requireArray(value, field, errors, { allowEmpty = false } = {}) {
  if (!Array.isArray(value) || (!allowEmpty && value.length === 0)) {
    errors.push(`${field} must be ${allowEmpty ? "an array" : "a non-empty array"}`);
    return false;
  }
  return true;
}

function requirePattern(value, field, pattern, label, errors) {
  if (typeof value !== "string" || !pattern.test(value)) {
    errors.push(`${field} must be ${label}`);
    return false;
  }
  return true;
}

function validateDirtyState(value, field, errors) {
  if (!requireObject(value, field, errors)) return;
  if (value.captured !== true) {
    errors.push(`${field}.captured must be true`);
  }
  if (!requireArray(value.entries, `${field}.entries`, errors, { allowEmpty: true })) return;

  value.entries.forEach((entry, index) => {
    const prefix = `${field}.entries.${index}`;
    if (!requireObject(entry, prefix, errors)) return;
    requireString(entry.status, `${prefix}.status`, errors);
    requireString(entry.path, `${prefix}.path`, errors);
    if (!hasOwn(entry, "contentSha256")) {
      errors.push(`${prefix}.contentSha256 must be explicit`);
    } else if (entry.contentSha256 !== null) {
      requirePattern(entry.contentSha256, `${prefix}.contentSha256`, SHA256, "a lowercase SHA-256", errors);
    }
  });
}

function validateRepositories(value, errors) {
  if (!requireArray(value, "repositories", errors)) return;
  const ids = new Set();

  value.forEach((repository, index) => {
    const prefix = `repositories.${index}`;
    if (!requireObject(repository, prefix, errors)) return;
    if (requireString(repository.id, `${prefix}.id`, errors)) {
      if (ids.has(repository.id)) errors.push(`${prefix}.id must be unique`);
      ids.add(repository.id);
    }
    requireString(repository.root, `${prefix}.root`, errors);
    requireString(repository.branch, `${prefix}.branch`, errors);
    requirePattern(repository.headCommit, `${prefix}.headCommit`, GIT_COMMIT, "a 40- or 64-character Git commit", errors);
    validateDirtyState(repository.dirtyState, `${prefix}.dirtyState`, errors);

    if (!requireArray(repository.submodules, `${prefix}.submodules`, errors, { allowEmpty: true })) return;
    const submodulePaths = new Set();
    repository.submodules.forEach((submodule, submoduleIndex) => {
      const subPrefix = `${prefix}.submodules.${submoduleIndex}`;
      if (!requireObject(submodule, subPrefix, errors)) return;
      if (requireString(submodule.path, `${subPrefix}.path`, errors)) {
        if (submodulePaths.has(submodule.path)) errors.push(`${subPrefix}.path must be unique`);
        submodulePaths.add(submodule.path);
      }
      requirePattern(submodule.headCommit, `${subPrefix}.headCommit`, GIT_COMMIT, "a 40- or 64-character Git commit", errors);
      validateDirtyState(submodule.dirtyState, `${subPrefix}.dirtyState`, errors);
    });
  });
}

function validateRuntimes(value, errors) {
  if (!requireObject(value, "runtimes", errors)) return;
  for (const runtime of ["java", "maven", "python", "node", "npm", "container"]) {
    requireString(value[runtime], `runtimes.${runtime}`, errors);
  }
}

function validateLockfiles(value, repositoryIds, errors) {
  if (!requireArray(value, "lockfiles", errors)) return;
  value.forEach((lockfile, index) => {
    const prefix = `lockfiles.${index}`;
    if (!requireObject(lockfile, prefix, errors)) return;
    if (requireString(lockfile.repository, `${prefix}.repository`, errors) && !repositoryIds.has(lockfile.repository)) {
      errors.push(`${prefix}.repository must name a manifest repository`);
    }
    requireString(lockfile.path, `${prefix}.path`, errors);
    requirePattern(lockfile.sha256, `${prefix}.sha256`, SHA256, "a lowercase SHA-256", errors);
  });
}

function validateCommands(value, errors) {
  if (!requireArray(value, "commands", errors)) return;
  const ids = new Set();
  value.forEach((command, index) => {
    const prefix = `commands.${index}`;
    if (!requireObject(command, prefix, errors)) return;
    if (requireString(command.id, `${prefix}.id`, errors)) {
      if (ids.has(command.id)) errors.push(`${prefix}.id must be unique`);
      ids.add(command.id);
    }
    requireString(command.workingDirectory, `${prefix}.workingDirectory`, errors);
    if (requireArray(command.argv, `${prefix}.argv`, errors)) {
      command.argv.forEach((argument, argumentIndex) => {
        requireString(argument, `${prefix}.argv.${argumentIndex}`, errors);
      });
    }
  });
}

function validateVersionedFiles(value, field, errors) {
  if (!requireArray(value, field, errors)) return;
  value.forEach((group, index) => {
    const prefix = `${field}.${index}`;
    if (!requireObject(group, prefix, errors)) return;
    const errorsBeforeGroup = errors.length;
    requireString(group.id, `${prefix}.id`, errors);
    requireString(group.version, `${prefix}.version`, errors);
    if (!requireArray(group.files, `${prefix}.files`, errors)) return;
    group.files.forEach((file, fileIndex) => {
      const filePrefix = `${prefix}.files.${fileIndex}`;
      if (!requireObject(file, filePrefix, errors)) return;
      requireString(file.repository, `${filePrefix}.repository`, errors);
      requireString(file.path, `${filePrefix}.path`, errors);
      requirePattern(file.sha256, `${filePrefix}.sha256`, SHA256, "a lowercase SHA-256", errors);
    });
    if (errors.length === errorsBeforeGroup) {
      const expectedVersion = `sha256:${sha256Bytes(canonicalJson(group.files))}`;
      if (group.version !== expectedVersion) errors.push(`${prefix}.version mismatch`);
    }
  });
}

function validateConfiguration(value, errors) {
  if (!requireObject(value, "configuration", errors)) return;
  if (requireArray(value.modelPolicies, "configuration.modelPolicies", errors)) {
    value.modelPolicies.forEach((policy, index) => {
      const prefix = `configuration.modelPolicies.${index}`;
      if (!requireObject(policy, prefix, errors)) return;
      requireString(policy.purpose, `${prefix}.purpose`, errors);
      if (!requireString(policy.status, `${prefix}.status`, errors)) return;
      if (policy.status === "CONFIGURED") {
        requireString(policy.providerId, `${prefix}.providerId`, errors);
        requireString(policy.modelId, `${prefix}.modelId`, errors);
      } else {
        if (!hasOwn(policy, "providerId")) errors.push(`${prefix}.providerId must be explicit`);
        if (!hasOwn(policy, "modelId")) errors.push(`${prefix}.modelId must be explicit`);
        requireString(policy.reason, `${prefix}.reason`, errors);
      }
    });
  }
  validateVersionedFiles(value.prompts, "configuration.prompts", errors);
  validateVersionedFiles(value.rules, "configuration.rules", errors);
  if (requireObject(value.index, "configuration.index", errors)) {
    requireString(value.index.status, "configuration.index.status", errors);
    requireString(value.index.version, "configuration.index.version", errors);
    if (!hasOwn(value.index, "generationId")) {
      errors.push("configuration.index.generationId must be explicit");
    } else if (value.index.status === "READY") {
      requireString(value.index.generationId, "configuration.index.generationId", errors);
    }
    if (value.index.status !== "READY") {
      requireString(value.index.reason, "configuration.index.reason", errors);
    }
  }
}

function validateFixtures(value, errors) {
  if (!requireArray(value, "fixtures", errors)) return;
  value.forEach((fixture, index) => {
    const prefix = `fixtures.${index}`;
    if (!requireObject(fixture, prefix, errors)) return;
    requireString(fixture.id, `${prefix}.id`, errors);
    requireString(fixture.repository, `${prefix}.repository`, errors);
    requireString(fixture.path, `${prefix}.path`, errors);
    requirePattern(fixture.sha256, `${prefix}.sha256`, SHA256, "a lowercase SHA-256", errors);
  });
}

function validateSeeds(value, errors) {
  if (!requireArray(value, "randomSeeds", errors)) return;
  value.forEach((seed, index) => {
    const prefix = `randomSeeds.${index}`;
    if (!requireObject(seed, prefix, errors)) return;
    requireString(seed.id, `${prefix}.id`, errors);
    if (!Number.isSafeInteger(seed.value)) errors.push(`${prefix}.value must be a safe integer`);
  });
}

async function validateArtifacts(value, manifestDirectory, errors) {
  if (!requireArray(value, "artifacts", errors)) return;
  const ids = new Set();
  await Promise.all(
    value.map(async (artifact, index) => {
      const prefix = `artifacts.${index}`;
      if (!requireObject(artifact, prefix, errors)) return;
      if (requireString(artifact.id, `${prefix}.id`, errors)) {
        if (ids.has(artifact.id)) errors.push(`${prefix}.id must be unique`);
        ids.add(artifact.id);
      }
      if (!requireString(artifact.path, `${prefix}.path`, errors)) return;
      if (!requirePattern(artifact.sha256, `${prefix}.sha256`, SHA256, "a lowercase SHA-256", errors)) return;

      try {
        const absolutePath = await safeInputPath(manifestDirectory, artifact.path, `${prefix}.path`);
        const actual = sha256Bytes(await readFile(absolutePath));
        if (actual !== artifact.sha256) {
          errors.push(`${prefix}.sha256 mismatch for ${artifact.path}`);
        }
      } catch (error) {
        errors.push(`${prefix}.path cannot be read: ${error.message}`);
      }
    }),
  );
}

function sortedEntries(entries) {
  return [...entries].sort((left, right) =>
    `${left.status}\u0000${left.path}\u0000${left.contentSha256 ?? ""}`.localeCompare(
      `${right.status}\u0000${right.path}\u0000${right.contentSha256 ?? ""}`,
    ),
  );
}

function compareDirtyState(expected, actual, field, errors) {
  if (canonicalJson(sortedEntries(expected.entries)) !== canonicalJson(sortedEntries(actual))) {
    errors.push(`${field}.entries mismatch`);
  }
}

async function verifyRepositories(repositories, inspectRepository, errors) {
  for (const repository of repositories) {
    let actual;
    try {
      actual = await inspectRepository(repository);
    } catch (error) {
      errors.push(`repositories.${repository.id} cannot be inspected: ${error.message}`);
      continue;
    }
    const prefix = `repositories.${repository.id}`;
    if (repository.headCommit !== actual.headCommit) errors.push(`${prefix}.headCommit mismatch`);
    if (repository.branch !== actual.branch) errors.push(`${prefix}.branch mismatch`);
    compareDirtyState(repository.dirtyState, actual.dirtyEntries, `${prefix}.dirtyState`, errors);

    const actualSubmodules = new Map(actual.submodules.map((submodule) => [submodule.path, submodule]));
    for (const submodule of repository.submodules) {
      const actualSubmodule = actualSubmodules.get(submodule.path);
      if (!actualSubmodule) {
        errors.push(`${prefix}.submodules.${submodule.path} is missing`);
        continue;
      }
      if (submodule.headCommit !== actualSubmodule.headCommit) {
        errors.push(`${prefix}.submodules.${submodule.path}.headCommit mismatch`);
      }
      compareDirtyState(
        submodule.dirtyState,
        actualSubmodule.dirtyEntries,
        `${prefix}.submodules.${submodule.path}.dirtyState`,
        errors,
      );
    }
    if (actualSubmodules.size !== repository.submodules.length) {
      errors.push(`${prefix}.submodules inventory mismatch`);
    }
  }
}

async function verifySourceRecord(record, field, sourceRoots, errors) {
  const sourceRoot = sourceRoots.get(record.repository);
  if (sourceRoot === undefined) {
    errors.push(`${field}.repository must name workspace or a manifest repository`);
    return;
  }
  try {
    const absolutePath = await safeInputPath(sourceRoot, record.path, `${field}.path`);
    const actual = sha256Bytes(await readFile(absolutePath));
    if (actual !== record.sha256) errors.push(`${field}.sha256 mismatch for ${record.path}`);
  } catch (error) {
    errors.push(`${field}.path cannot be read: ${error.message}`);
  }
}

async function verifySourceInputs(manifest, errors) {
  const sourceRoots = new Map([
    ["workspace", manifest.workspace.root],
    ...manifest.repositories.map((repository) => [repository.id, repository.root]),
  ]);
  const records = [
    ...manifest.lockfiles.map((record, index) => [record, `lockfiles.${index}`]),
    ...manifest.configuration.prompts.flatMap((group, groupIndex) =>
      group.files.map((record, fileIndex) => [record, `configuration.prompts.${groupIndex}.files.${fileIndex}`]),
    ),
    ...manifest.configuration.rules.flatMap((group, groupIndex) =>
      group.files.map((record, fileIndex) => [record, `configuration.rules.${groupIndex}.files.${fileIndex}`]),
    ),
    ...manifest.fixtures.map((record, index) => [record, `fixtures.${index}`]),
  ];
  await Promise.all(
    records.map(([record, field]) => verifySourceRecord(record, field, sourceRoots, errors)),
  );
}

async function validateEnvironmentIdentity(manifest, manifestDirectory, errors) {
  if (!Array.isArray(manifest.artifacts) || !requireObject(manifest.workspace, "workspace", errors)) return;
  const environmentArtifacts = manifest.artifacts.filter((artifact) => artifact?.id === "environment");
  if (environmentArtifacts.length !== 1) {
    errors.push("artifacts must contain exactly one environment identity artifact");
    return;
  }
  const environmentArtifact = environmentArtifacts[0];
  if (manifest.workspace.environmentFingerprintSha256 !== environmentArtifact.sha256) {
    errors.push("workspace.environmentFingerprintSha256 mismatch with environment artifact");
  }
  if (typeof environmentArtifact.path !== "string") return;
  try {
    const absolutePath = await safeInputPath(manifestDirectory, environmentArtifact.path, "environment artifact");
    const payload = JSON.parse(await readFile(absolutePath, "utf8"));
    if (canonicalJson(payload.runtimes) !== canonicalJson(manifest.runtimes)) {
      errors.push("environment runtimes mismatch with manifest runtimes");
    }
  } catch (error) {
    errors.push(`environment artifact cannot be validated: ${error.message}`);
  }
}

export async function validateManifest(
  manifest,
  { manifestDirectory = process.cwd(), inspectRepository, verifyWorkspace = false } = {},
) {
  const errors = [];
  if (!requireObject(manifest, "manifest", errors)) {
    return { valid: false, errors, manifestSha256: sha256Bytes(canonicalJson(manifest)) };
  }

  if (manifest.schemaVersion !== SCHEMA_VERSION) {
    errors.push(`schemaVersion must equal ${SCHEMA_VERSION}`);
  }
  if (!requireString(manifest.capturedAt, "capturedAt", errors) || Number.isNaN(Date.parse(manifest.capturedAt))) {
    errors.push("capturedAt must be an ISO-8601 timestamp");
  }
  if (requireObject(manifest.workspace, "workspace", errors)) {
    requireString(manifest.workspace.root, "workspace.root", errors);
    requirePattern(
      manifest.workspace.environmentFingerprintSha256,
      "workspace.environmentFingerprintSha256",
      SHA256,
      "a lowercase SHA-256",
      errors,
    );
  }
  validateRepositories(manifest.repositories, errors);
  validateRuntimes(manifest.runtimes, errors);
  const repositoryIds = new Set(
    Array.isArray(manifest.repositories) ? manifest.repositories.map((repository) => repository?.id) : [],
  );
  validateLockfiles(manifest.lockfiles, repositoryIds, errors);
  validateCommands(manifest.commands, errors);
  validateConfiguration(manifest.configuration, errors);
  validateFixtures(manifest.fixtures, errors);
  validateSeeds(manifest.randomSeeds, errors);
  await validateArtifacts(manifest.artifacts, path.resolve(manifestDirectory), errors);
  await validateEnvironmentIdentity(manifest, path.resolve(manifestDirectory), errors);

  if (verifyWorkspace) {
    if (typeof inspectRepository !== "function") {
      errors.push("workspace verification requires inspectRepository");
    } else if (errors.length === 0 && Array.isArray(manifest.repositories)) {
      await verifyRepositories(manifest.repositories, inspectRepository, errors);
      await verifySourceInputs(manifest, errors);
    }
  }

  return {
    valid: errors.length === 0,
    errors,
    manifestSha256: sha256Bytes(canonicalJson(manifest)),
  };
}

export async function writeManifestBundle(manifestPath, manifest) {
  const contents = canonicalJson(manifest);
  const manifestSha256 = sha256Bytes(contents);
  await writeFile(manifestPath, contents, { encoding: "utf8", mode: 0o600 });
  await writeFile(
    `${manifestPath}.sha256`,
    `${manifestSha256}  ${path.basename(manifestPath)}\n`,
    { encoding: "utf8", mode: 0o600 },
  );
  return { manifestSha256 };
}

export async function verifyManifestBundle(manifestPath, options = {}) {
  const errors = [];
  let raw;
  let checksum;
  try {
    raw = await readFile(manifestPath, "utf8");
  } catch (error) {
    return { valid: false, errors: [`manifest cannot be read: ${error.code}`], manifestSha256: null };
  }
  try {
    checksum = (await readFile(`${manifestPath}.sha256`, "utf8")).trim().split(/\s+/u)[0];
  } catch (error) {
    errors.push(`detached checksum cannot be read: ${error.code}`);
  }

  const manifestSha256 = sha256Bytes(raw);
  if (checksum !== undefined && checksum !== manifestSha256) {
    errors.push("detached checksum mismatch");
  }

  let manifest;
  try {
    manifest = JSON.parse(raw);
  } catch (error) {
    errors.push(`manifest JSON is invalid: ${error.message}`);
    return { valid: false, errors, manifestSha256 };
  }

  const validation = await validateManifest(manifest, {
    ...options,
    manifestDirectory: path.dirname(path.resolve(manifestPath)),
  });
  return {
    valid: errors.length === 0 && validation.valid,
    errors: [...errors, ...validation.errors],
    manifestSha256,
  };
}

import { execFile as execFileCallback } from "node:child_process";
import { lstat, readFile, readlink } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";

import { sha256Bytes } from "./manifest-validator.mjs";

const execFile = promisify(execFileCallback);

async function git(root, args) {
  const { stdout } = await execFile("git", ["-C", root, ...args], {
    encoding: "utf8",
    maxBuffer: 16 * 1024 * 1024,
  });
  return stdout;
}

export async function digestRepositoryPath(root, relativePath) {
  const absolutePath = path.resolve(root, relativePath);
  const repositoryPrefix = `${path.resolve(root)}${path.sep}`;
  if (absolutePath !== path.resolve(root) && !absolutePath.startsWith(repositoryPrefix)) {
    throw new Error(`Git reported a path outside the repository: ${relativePath}`);
  }

  try {
    const stats = await lstat(absolutePath);
    if (stats.isSymbolicLink()) {
      return sha256Bytes(await readlink(absolutePath));
    }
    if (stats.isFile()) {
      return sha256Bytes(await readFile(absolutePath));
    }
    return null;
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

export function parseGitStatus(output) {
  const records = output.split("\u0000");
  if (records.at(-1) === "") records.pop();
  const entries = [];

  for (let index = 0; index < records.length; index += 1) {
    const record = records[index];
    if (record.length < 4 || record[2] !== " ") {
      throw new Error("Malformed git status --porcelain=v1 -z output");
    }
    const status = record.slice(0, 2);
    const filePath = record.slice(3);
    const entry = { status, path: filePath };
    if (status.includes("R") || status.includes("C")) {
      index += 1;
      if (index >= records.length) throw new Error("Missing original path for a rename/copy status");
      entry.originalPath = records[index];
    }
    entries.push(entry);
  }
  return entries;
}

async function inspectDirtyEntries(root) {
  const output = await git(root, [
    "status",
    "--porcelain=v1",
    "-z",
    "--untracked-files=all",
    "--ignore-submodules=none",
  ]);
  const entries = await Promise.all(
    parseGitStatus(output).map(async (entry) => ({
      ...entry,
      contentSha256: await digestRepositoryPath(root, entry.path),
    })),
  );
  return entries.sort((left, right) => left.path.localeCompare(right.path));
}

export function parseGitSubmoduleStatus(output) {
  if (output.trim() === "") return [];
  return output
    .trimEnd()
    .split("\n")
    .map((line) => {
      const match = /^(.)([a-f0-9]{40}(?:[a-f0-9]{24})?) (.+?)(?: \(.*\))?$/u.exec(line);
      if (!match) throw new Error(`Malformed git submodule status output: ${line}`);
      return { marker: match[1], headCommit: match[2], path: match[3] };
    });
}

async function inspectSubmodules(root) {
  const statuses = parseGitSubmoduleStatus(await git(root, ["submodule", "status", "--recursive"]));
  return Promise.all(
    statuses.map(async (submodule) => ({
      path: submodule.path,
      headCommit: submodule.headCommit,
      dirtyEntries:
        submodule.marker === "-"
          ? [{ status: "UN", path: ".", contentSha256: null }]
          : await inspectDirtyEntries(path.join(root, submodule.path)),
    })),
  );
}

export async function inspectGitRepository({ root }) {
  const [headCommit, branch, dirtyEntries, submodules] = await Promise.all([
    git(root, ["rev-parse", "HEAD"]).then((value) => value.trim()),
    git(root, ["branch", "--show-current"]).then((value) => value.trim() || "DETACHED"),
    inspectDirtyEntries(root),
    inspectSubmodules(root),
  ]);

  return { headCommit, branch, dirtyEntries, submodules };
}

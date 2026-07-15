import assert from "node:assert/strict";
import { execFile as execFileCallback } from "node:child_process";
import { mkdtemp, symlink, unlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import test from "node:test";

import {
  digestRepositoryPath,
  inspectGitRepository,
  parseGitStatus,
  parseGitSubmoduleStatus,
} from "../lib/workspace-inspector.mjs";
import { sha256Bytes } from "../lib/manifest-validator.mjs";

const execFile = promisify(execFileCallback);

async function git(root, ...args) {
  return execFile("git", ["-C", root, ...args], { encoding: "utf8" });
}

async function initializeRepository(root) {
  await execFile("git", ["init", "-b", "main", root]);
  await git(root, "config", "user.email", "baseline@example.invalid");
  await git(root, "config", "user.name", "Baseline Test");
  await writeFile(path.join(root, "tracked.txt"), "tracked\n", "utf8");
  await writeFile(path.join(root, "deleted.txt"), "delete me\n", "utf8");
  await git(root, "add", ".");
  await git(root, "commit", "-m", "initial");
}

test("captures exact commit, branch, dirty files, digests, and submodule state", async () => {
  const temp = await mkdtemp(path.join(os.tmpdir(), "codecrow-git-inspector-"));
  const child = path.join(temp, "child");
  const parent = path.join(temp, "parent");
  await initializeRepository(child);
  await initializeRepository(parent);
  await git(parent, "-c", "protocol.file.allow=always", "submodule", "add", child, "frontend");
  await git(parent, "commit", "-am", "add submodule");

  await writeFile(path.join(parent, "tracked.txt"), "changed\n", "utf8");
  await unlink(path.join(parent, "deleted.txt"));
  await writeFile(path.join(parent, "untracked name.txt"), "new\n", "utf8");
  await symlink("tracked.txt", path.join(parent, "tracked-link"));
  await writeFile(path.join(parent, "frontend", "tracked.txt"), "submodule changed\n", "utf8");

  const inspected = await inspectGitRepository({ id: "fixture", root: parent });
  const head = (await git(parent, "rev-parse", "HEAD")).stdout.trim();
  const childHead = (await git(path.join(parent, "frontend"), "rev-parse", "HEAD")).stdout.trim();

  assert.equal(inspected.headCommit, head);
  assert.equal(inspected.branch, "main");
  assert.deepEqual(
    inspected.dirtyEntries.map(({ status, path: filePath }) => [status, filePath]),
    [
      [" D", "deleted.txt"],
      [" M", "frontend"],
      ["??", "tracked-link"],
      [" M", "tracked.txt"],
      ["??", "untracked name.txt"],
    ],
  );
  assert.equal(
    inspected.dirtyEntries.find((entry) => entry.path === "tracked.txt").contentSha256,
    sha256Bytes("changed\n"),
  );
  assert.equal(
    inspected.dirtyEntries.find((entry) => entry.path === "deleted.txt").contentSha256,
    null,
  );
  assert.equal(
    inspected.dirtyEntries.find((entry) => entry.path === "tracked-link").contentSha256,
    sha256Bytes("tracked.txt"),
  );
  assert.deepEqual(inspected.submodules, [
    {
      path: "frontend",
      headCommit: childHead,
      dirtyEntries: [
        {
          status: " M",
          path: "tracked.txt",
          contentSha256: sha256Bytes("submodule changed\n"),
        },
      ],
    },
  ]);
});

test("records detached HEAD explicitly", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "codecrow-git-detached-"));
  await initializeRepository(root);
  await git(root, "checkout", "--detach");

  const inspected = await inspectGitRepository({ id: "fixture", root });

  assert.equal(inspected.branch, "DETACHED");
  assert.deepEqual(inspected.dirtyEntries, []);
  assert.deepEqual(inspected.submodules, []);
});

test("parses rename and copy status records without path quoting ambiguity", () => {
  assert.deepEqual(parseGitStatus("R  new name\u0000old name\u0000"), [
    { status: "R ", path: "new name", originalPath: "old name" },
  ]);
  assert.deepEqual(parseGitStatus("C  copy name\u0000source name\u0000"), [
    { status: "C ", path: "copy name", originalPath: "source name" },
  ]);
  assert.deepEqual(parseGitStatus(" M tracked.txt"), [{ status: " M", path: "tracked.txt" }]);
  assert.throws(() => parseGitStatus("bad"), /Malformed git status/);
  assert.throws(() => parseGitStatus("R  renamed\u0000"), /Missing original path/);
});

test("parses submodule states and rejects malformed output", () => {
  const commit = "a".repeat(40);
  assert.deepEqual(parseGitSubmoduleStatus(` ${commit} frontend (heads/main)\n`), [
    { marker: " ", headCommit: commit, path: "frontend" },
  ]);
  assert.deepEqual(parseGitSubmoduleStatus(`-${commit} nested/path\n`), [
    { marker: "-", headCommit: commit, path: "nested/path" },
  ]);
  assert.throws(() => parseGitSubmoduleStatus("invalid\n"), /Malformed git submodule status/);
});

test("repository-path digest refuses traversal and propagates non-missing filesystem errors", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "codecrow-path-digest-"));
  await assert.rejects(() => digestRepositoryPath(root, "../outside"), /outside the repository/);
  await assert.rejects(() => digestRepositoryPath(root, "bad\u0000path"));
  assert.equal(await digestRepositoryPath(root, "."), null);
});

test("records an uninitialized submodule explicitly", async () => {
  const temp = await mkdtemp(path.join(os.tmpdir(), "codecrow-git-uninitialized-"));
  const child = path.join(temp, "child");
  const parent = path.join(temp, "parent");
  await initializeRepository(child);
  await initializeRepository(parent);
  await git(parent, "-c", "protocol.file.allow=always", "submodule", "add", child, "frontend");
  await git(parent, "commit", "-am", "add submodule");
  await git(parent, "submodule", "deinit", "-f", "frontend");

  const inspected = await inspectGitRepository({ id: "fixture", root: parent });

  assert.equal(inspected.submodules[0].path, "frontend");
  assert.deepEqual(inspected.submodules[0].dirtyEntries, [
    { status: "UN", path: ".", contentSha256: null },
  ]);
});

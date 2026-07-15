import { lstat, mkdir, realpath } from "node:fs/promises";
import path from "node:path";

function assertContained(root, candidate, label) {
  const relative = path.relative(root, candidate);
  if (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error(`${label} must stay within its declared root`);
  }
}

function lexicalPath(root, relativePath, label) {
  const absoluteRoot = path.resolve(root);
  const candidate = path.resolve(absoluteRoot, relativePath);
  assertContained(absoluteRoot, candidate, label);
  return { absoluteRoot, candidate };
}

export async function safeInputPath(root, relativePath, label) {
  const { absoluteRoot, candidate } = lexicalPath(root, relativePath, label);
  const stats = await lstat(candidate);
  if (stats.isSymbolicLink()) throw new Error(`${label} must not be a symbolic link`);
  const [resolvedRoot, resolvedCandidate] = await Promise.all([
    realpath(absoluteRoot),
    realpath(candidate),
  ]);
  assertContained(resolvedRoot, resolvedCandidate, label);
  return resolvedCandidate;
}

export async function safeOutputPath(root, relativePath, label) {
  const { absoluteRoot, candidate } = lexicalPath(root, relativePath, label);
  await mkdir(absoluteRoot, { recursive: true, mode: 0o700 });
  const parent = path.dirname(candidate);
  await mkdir(parent, { recursive: true });
  const [resolvedRoot, resolvedParent] = await Promise.all([
    realpath(absoluteRoot),
    realpath(parent),
  ]);
  assertContained(resolvedRoot, resolvedParent, label);
  try {
    const stats = await lstat(candidate);
    if (stats.isSymbolicLink()) throw new Error(`${label} must not be a symbolic link`);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  return candidate;
}

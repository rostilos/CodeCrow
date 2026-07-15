#!/usr/bin/env node

import path from "node:path";

import { verifyManifestBundle } from "../lib/manifest-validator.mjs";
import { inspectGitRepository } from "../lib/workspace-inspector.mjs";

const manifestPath = path.resolve(process.argv[2] ?? ".llm-handoff-artifacts/p0-01/baseline-manifest.json");
const result = await verifyManifestBundle(manifestPath, {
  inspectRepository: inspectGitRepository,
  verifyWorkspace: true,
});

process.stdout.write(`${JSON.stringify({ manifestPath, ...result }, null, 2)}\n`);
if (!result.valid) process.exitCode = 1;

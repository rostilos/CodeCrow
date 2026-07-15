#!/usr/bin/env node

import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { captureBaseline } from "../lib/baseline-capture.mjs";
import {
  buildCurrentBaselineSpec,
  probeRuntimeVersions,
} from "../lib/current-baseline-spec.mjs";
import { inspectGitRepository } from "../lib/workspace-inspector.mjs";

const binRoot = path.dirname(fileURLToPath(import.meta.url));
const publicRoot = path.resolve(binRoot, "../../..");
const workspaceRoot = path.dirname(publicRoot);
const spec = buildCurrentBaselineSpec({
  artifactRoot: path.join(publicRoot, ".llm-handoff-artifacts", "p0-01"),
  capturedAt: new Date().toISOString(),
  environment: {
    platform: process.platform,
    release: os.release(),
    architecture: os.arch(),
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  },
  inspectRepository: inspectGitRepository,
  publicRoot,
  runtimes: await probeRuntimeVersions(),
  staticRoot: path.join(workspaceRoot, "codecrow-static"),
  workspaceRoot,
});

process.stdout.write(`${JSON.stringify(await captureBaseline(spec), null, 2)}\n`);

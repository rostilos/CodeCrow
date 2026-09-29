# Repository-index capacity investigation — 2026-09-28

The benchmark and deployed services were inspected read-only. No benchmark,
service image build, restart, redeployment, runtime configuration change, or database
mutation was performed as part of this investigation.

## Observed bottleneck

`Waiting for repository-index capacity` originates in Java's durable `Job`
intake. `RepositoryIndexLifecycleScheduler` dispatches those jobs through
`BranchIndexBuildExecutorConfiguration`. Its effective default was **one Java
worker**, which stays occupied through branch-head resolution, acquisition and
the entire Python indexing request. The mounted Java configuration did not
override that property.

The database snapshot contained **one running and sixteen pending** repository
index jobs. Recent successful jobs ran sequentially: one from 14:17:11 to
14:19:52 UTC, the next from 14:19:57 to 14:35:02, followed by a build from
14:35:08 to 14:55:05. The next acquired the sole worker at 14:55:14.
Several current-generation jobs had been pending since 14:16:05.

The user-set `RAG_FULL_INDEX_CONCURRENCY=40` was present in the RAG service's
mounted `.env`, and that service used one Uvicorn process. This is the Python
manager's per-process admission semaphore for full, delta and proposed-tree
structural builds. It does not change Java's executor. Logs showed direct
benchmark builds and a managed-project build reaching Python concurrently.
Mutation leases are scoped to tenant/project/exact generation and do not impose
one global cross-tenant lock.

Read-only health probes during the running benchmark returned HTTP 200:
`/health` in 11 ms and `/system/representation` in 27 ms. Those endpoints do not
open generation stores. Health gating was not the observed bottleneck.

## Misleading waiting reasons

Twelve older pending jobs had repeated provider-access diagnostics such as
missing app access tokens or failed token decryption while retaining the same
capacity message. These failures need connection repair; increasing concurrency
does not repair credentials. Existing durable retries rotate such jobs behind
untouched work, so the pending rows were not lost ephemeral queue entries.

Provider branch-head lookup failures now update only the current step and
activity time of an unclaimed repository-index job to
`Waiting for repository access; branch-head lookup will retry`. The detailed
existing warning remains in the job log. The atomic update requires the job to
still be `PENDING` and of the repository-index type; it cannot overwrite a
concurrent claim, revision, running job or terminal outcome.

## Implemented changes

- Java dispatch defaults to four orchestration workers rather than serializing
  unrelated repositories. The canonical property remains
  `codecrow.rag.branch-build.global-parallelism`.
- Compose exposes `RAG_BRANCH_BUILD_PARALLELISM` in `deployment/.env` and maps it
  to `CODECROW_RAG_BRANCH_BUILD_GLOBAL_PARALLELISM`. Startup logs the effective
  Java capacity and identifies the separate Python setting.
- The executor retains zero in-memory backlog. Rejected work remains durable in
  the database; branch ordering, atomic claims, generation recovery and tenant
  isolation remain intact.
- Python no longer silently rewrites an explicit
  `RAG_FULL_INDEX_CONCURRENCY` to one when multiple Uvicorn processes are used.
  Startup reports per-process and aggregate slots. Existing config validation
  and minimum-one normalization remain unchanged.
- Deployment examples describe both stages and remove the unused
  `codecrow.rag.branch-build.parallelism` setting. The Python sample now
  accurately shows the existing default of one slot per process.

Java dispatch capacity is per pipeline-agent replica. Python admission capacity
is per process: configured slots × Uvicorn processes × RAG replicas is an upper
bound, not a measured throughput promise. Source parsing and finalization still
consume CPU and memory; increasing an admission setting does not create compute.
Configuration is loaded at service startup, not live when a mounted file changes.
The changes have not been applied to the running benchmark deployment.

## Verification

- 123 Java unit/compatibility tests passed with no failures or skips. They cover
  actual executor concurrency, configured/environment capacity, durable overflow,
  worker recovery/shutdown, provider lookup deferral and retry, claim preservation,
  supported PR providers, branch/manual/comment paths and degraded/restart recovery.
- 19 Python startup/configuration tests passed. Explicit multi-process capacity,
  default behavior, invalid settings and unchanged environment are covered.
- Base, local-build and production Compose configurations each passed default
  and overridden dispatch-capacity rendering (six checks), without deployment.
- Ten existing Docs files were updated. An isolated `npm run build` passed
  with 150 canonical pages and 6,268 anchors. Twelve internal link targets were
  checked against application routes, and the built sources match working files.
  Build output remains in `/tmp/codecrow-index-capacity-docs-pvn9ci8w`.
- Whitespace/diff checks passed in both repositories.

No throughput or latency improvement is claimed without a controlled load
comparison. Existing pending work does not need to be resubmitted after an
operator later deploys the fix; the durable dispatcher can recover it. Repository
credential failures still require correction of the affected connection.

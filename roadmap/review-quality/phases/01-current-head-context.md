# Phase 1 — Current-head incremental context

**Status: Done.**

## Outcome

Stage 1 reviews only the newest trustworthy delta. RAG, exact reads, and PR file
snapshots represent every change still active between the PR base and current
head. This repairs run-1/run-2 context loss without repeatedly reviewing old
hunks.

## I1 — Preserve a complete provider manifest before filtering

Add one provider-neutral PR manifest result containing every base-to-head path,
change kind, old path for renames, and a completeness receipt. Provider adapters
must follow native pagination/truncation rules; a raw patch string or a file entry
without a patch is not proof that the manifest is complete.

Capture this manifest before language, generated-file, path, or analysis-scope
filtering. Scope filtering then produces reviewable `rawDiff`/`deltaDiff`, while
the full manifest drives overlay and snapshot maintenance. Maintenance still runs
when the direct review delta contains no reviewable files.

Extend the existing Java-to-Python request flow with full-manifest paths and
deletions; do not add a second analysis endpoint:

- `rawDiff`: reviewable base-to-current PR text;
- `deltaDiff`: reviewable compatible-predecessor-to-current text;
- `changedFiles`/`deletedFiles`: delta-only direct-review paths;
- full PR manifest: complete path/status data for current-head context.

An incomplete provider manifest permits reduced direct review when possible but
cannot replace or bind a supposedly complete current-head PR overlay.

## I2 — Select a trustworthy incremental predecessor

Select a successfully persisted analysis for the same PR and base lineage.
Persist the provider-native reviewed base SHA on each PR analysis and add the
minimal provider-neutral ancestry operation needed to prove that its head is an
ancestor of current head.

If base identity or ancestry cannot be proved, use the existing full-review
fallback. Force-push, rebase/base change, missing exact SHA, failed compare, and
non-ancestor histories also use full review. Provider-native compare success alone
is not treated as ancestry proof.

## I3 — Build an exact full-PR overlay

Classify each manifest path with the existing indexing-eligibility policy. Binary,
generated, excluded, unsupported, and size-limited files receive explicit skipped
dispositions and do not make the text overlay partial. Fetch immutable current-head
content for every eligible text path.

Pass the full processed source snapshot to `_index_pr_files`; continue passing the
delta processed diff to Stage 0/1. Replace the overlay only when the manifest is
complete and every eligible text path has exact content. If an eligible path is
partial or unavailable:

- continue core delta review with an observable reduced-context diagnostic;
- do not bind or query the incomplete overlay as the current commit;
- keep an older complete generation addressable only by its old revision, never
  as evidence for the new head.

Use complete-manifest differences for RAG and persisted snapshots: deletion writes
an old-path tombstone; revert removes the former overlay path; rename removes the
old path and indexes the new; re-add indexes new current content. Provider
metadata remains authoritative when no textual patch exists: a metadata-only
deletion still tombstones its path, and a metadata-only rename tombstones the old
path while exact current-head content supplies the destination. A complete empty
base-to-head manifest is a maintenance result that prunes stale snapshot and
overlay members, not a no-op. Delta-only deletions are not sufficient for full-PR
stale-context filtering.

## I4 — Make normal and cached paths maintain the same snapshot

Exact-result, commit-hash, and fingerprint cache hits returned before normal
Python indexing. Production PR result reuse is disabled so every request reaches
the full-manifest overlay/snapshot path. The dormant clone helpers clear source
lineage before destination matching; cache reuse can be restored later only if it
maintains the same current-head context contract.

Persist an internal `analysisBehaviorDigest` and durable `analysisRunKey` on
`CodeAnalysis`. The behavior hash changes with prompts, output schemas, evidence
gates, or pipeline membership. The run key is derived from the persisted outer
job plus project/PR, confirmed base/head, and behavior. Replaying that exact
accepted job and snapshot is idempotent; a newly accepted intentional rerun at
the same head creates a fresh occurrence instead of silently returning old
findings.

## I5 — Runtime proof

Run 1 adds A, run 2 adds B, run 3 adds C, and run 4 adds D. Run 4 directly reviews
D while exact current-head retrieval exposes A+B+C+D. Exercise the normal path;
production result-cache shortcuts remain disabled so they cannot bypass this
maintenance.

Also cover: no-reviewable-delta maintenance; provider pagination/truncation;
eligible partial source; deterministic skip dispositions; delete, revert, rename,
re-add; force-push; base change; and same-head behavior-hash change.

Phase 1 is complete when those behaviors pass and an incomplete/stale overlay can
never be advertised under the current head.

Recorded verification: the inference-orchestrator unit and integration suites
passed 1,609 tests,
the RAG pipeline unit suite passed 1,076 tests, and the affected Java 20-module
reactor completed with `BUILD SUCCESS`.

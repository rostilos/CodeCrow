# Phase 2 — Hybrid review and exact evidence

**Status: Done.**

## Outcome

The host guarantees universal hunk coverage; the LLM adds review judgment. Stage
1 may obtain one bounded exact evidence set before finalizing its candidates.

## P1 — Mandatory review units and packing

Create one host-owned unit for every reviewable hunk. A unit always contains the
hunk identity, changed lines, and a current-head source window. Add an enclosing
symbol only when enrichment provides reliable scope ranges; a symbol name alone
is not treated as a boundary. Exact plugin/parser relationships are optional
evidence and cannot remove a hunk from review.

Deterministically pack directly related units under the existing 60k-token Stage
1 target. Small independent units may share a call through isolated envelopes;
unrelated source is not compressed into one ambiguous context block.

## P2 — Annotation-only LLM planning

Send compact mandatory-unit headers to the existing single planning call. It may
add risk ordering and falsifiable defect hypotheses. It does not own coverage,
unit grouping, or retrieval. Invented paths are discarded; invalid or failed
output leaves the deterministic plan unchanged.

The planner receives a compact bounded header set. When it cannot include every
header, every omitted unit still retains its mandatory universal Stage 1 review.

Every unit receives the same general correctness, security/authorization,
data-flow, state, resource, concurrency, validation, and error-handling review.
Risk lenses add focus only.

## P3 — Existing issues with internal causal evidence

Continue using `CodeReviewIssue`. Add internal, serialization-excluded fields:

- `triggerCondition`;
- `causalPath`;
- `observableImpact`.

The existing ledger records those values with unit, hunk, exact evidence
references, revision, and content digests. They do not enter the database or
public response. Discovery produces compact issue facts and a short remediation,
not patches, resolution fields, confidence essays, file summaries, or final-report
prose for candidates that may later be rejected.

Extend the Stage 1 batch response with `contextRequests`. Each request names a
falsifiable question, target path or symbol/relationship, and the evidence needed
to confirm or reject the causal claim. Generic “more context” requests are
ignored.

## P4 — One exact-evidence continuation

A small host resolver processes requests with bounded read concurrency:

1. reuse complete enrichment content;
2. use parser metadata or revision-bound RAG only to suggest candidate paths;
3. read the exact window from the analyzed immutable head;
4. cache revision/path/range/digest receipts;
5. resume the originating pack once with successful results.

The normal Stage 1 path uses this resolver; it is not conditional on optional MCP
mode. Any MCP-backed read must be pinned to current head rather than target branch.
Unavailable optional context records a diagnostic and makes only dependent claims
incomplete.

A local evidence question consumes the continuation route. A question requiring
a repository interaction becomes a Phase 3 ticket instead and does not also
resume Stage 1. Local and cross-file follow-ups share the four-call per-review
budget. Register only the final Stage 1 response after its continuation, never
both provisional and final candidates.

## P5 — Clean discovery behavior

Use real system/user model roles. Remove historical issue prose, lifecycle
reconciliation, and mandatory duplicate-generation instructions from Stage 1.
Malformed output is incomplete after deterministic parsing; it does not invoke an
LLM JSON-repair call.

Phase 2 is complete when coverage cannot be reduced by planning, all evidence is
revision-bound, request/call budgets hold, and failed optional retrieval does not
fail unrelated review work.

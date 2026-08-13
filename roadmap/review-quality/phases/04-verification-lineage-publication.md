# Phase 4 — Verification, lineage, and publication

**Status: Done.** The active final-stage modules are `verification_wave.py` and
`report_renderer.py`.

## Outcome

Every local and cross-file candidate receives the same current-head verification.
Only confirmed current defects are published; old issues remain queryable without
being copied into a new report.

## V1 — Exact deterministic gates and evidence packets

Deterministic checks validate only facts code can establish: analyzed revision,
path/range, hunk/snippet digest, evidence-reference ownership, plugin/analyzer
contradictions, and ledger provenance. Semantic causality belongs to the verifier.
Historical IDs receive no exemption.

Before verification, merge only identical causal/evidence fingerprints. Build
candidate-only packets from the shared cache: changed trigger, relevant source
window, internal causal fields, exact related locations, and contradictions. Do
not resend the full Stage 1 prompt or whole files. Shared source appears once per
packet and the complete packet stays under the configured 120,000-character
boundary.

## V2 — One adversarial verification wave

Run after Stage 1 and cross-file discovery. Each packet receives one fresh model
call, with no generation transcript and no iterative substring/tool loop. For
each candidate return:

- `CONFIRMED`;
- `REJECTED`;
- `INCOMPLETE`.

Only confirmed candidates proceed. Malformed/failed packets become incomplete and
do not fail unrelated packets or the core review. Up to eight candidates fit a
packet and at most four packets run per review under existing per-review
concurrency.

The same configured model may be used with an adversarial prompt; this is not
described as independent-model consensus.

## V3 — Evidence-based deduplication

Merge exact causal/evidence fingerprints directly. The verifier is explicitly
instructed not to merge additional candidates, and any returned `duplicateOf`
value is ignored. Similar prose, nearby lines, category, or severity alone never
merge findings. A genuine defect with a wrong category/severity remains a true
issue.

The confirmed issue set is authoritative at Java ingestion. Remove the later
normalized-title/root-prose merge and the rule that drops non-resolution `INFO`
issues. Ingestion may validate and persist fields, but it cannot silently change
verified issue membership.

## V4 — Replace copy-forward with all-run lineage

Remove unmatched-OPEN append behavior and replace newest-only
`PrIssueTrackingService` matching/resolution with post-verification all-run
matching.

Derive lineage tips from existing rows and `trackedFromIssueId`, accepting an edge
only when its predecessor is older and belongs to the same tenant, project,
repository, and PR. Ignore and diagnose dangling, cross-scope, cyclic, or
otherwise invalid stored edges. Multiple valid children remain distinct tips;
one current candidate may match only one active tip and vice versa.

Before any database ID lookup, scope or discard discovery-supplied historical IDs.
Compute and persist a category-independent `lineageFingerprint` from the verified
trigger symbol/anchor, causal-path evidence identities, related locations, and
impact identity. Match directly against all scoped database rows using that
fingerprint and exact anchors; do not use the lossy previous-issue request DTO.
An ambiguous match becomes a new root. A match creates a new current occurrence with
`trackedFromIssueId = activeTip.id`; it never reuses the old primary key or old
prose. A recurrence of a closed tip is a new root.

Cached cross-PR clones must clear the source issue's `trackedFromIssueId` and
`lineageFingerprint`, then run this destination-PR matcher before publication.
Only an idempotent reuse of the exact same analysis record may retain its links.

Do not resolve an old issue because it was omitted, its snippet moved, or its
anchor disappeared. Without issue-specific proof that the defect condition is
absent, it remains historically active/not revalidated. No history-revalidation
call is added to this roadmap.

## V5 — Keep current findings and history disjoint

Define presentation sets explicitly:

- `currentFindings`: unresolved confirmed occurrences created by the current
  analysis;
- `historicalActiveNotRevalidated`: unresolved valid lineage tips whose occurrence
  belongs to an earlier analysis.

A linked current occurrence is the active tip and therefore appears only in
`currentFindings`. VCS reports and quality evaluation receive only current
findings. History APIs/file views may show the disjoint historical set with a
derived “not revalidated” label; no new persisted lifecycle state is required.
Implement this by replacing the existing latest-version query projection, not by
adding a second issue API or history store. It is required so removing copy-forward
does not make run-1/run-2 active history disappear from the product.

## V6 — Deterministic publication

Remove authoritative Stage 3 aggregation and model semantic-dedup calls. Render
the report directly from confirmed, deduplicated, lineage-linked structured
issues. Rendering may format those fields but cannot author, delete, resolve, or
rewrite findings.

## V7 — Documentation, builds, and runtime handoff

The existing public developer pages now describe analysis, RAG, incremental
history, retrieval, verification, failure, cost, deletion-only, and metadata-only
rename behavior. Recorded implementation evidence is 1,609 passing inference
unit/integration tests, 1,076 passing RAG unit tests,
a successful Java 20-module reactor, and successful frontend and public Docs
production builds.

Phase 4 is complete when stale history never enters current publication, every
candidate uses the common one-call verifier, distinct nearby defects survive,
tenant-scoped lineage tests pass, rendered issue membership is stable, and the
affected builds complete. The owner then runs the existing benchmark manually.
No corpus, automatic benchmark, A/B test, canary, or shadow run is introduced.

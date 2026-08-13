# Review quality implementation roadmap

## Objective

Change the review engine so it finds more real defects and publishes fewer
unsupported ones. The target is 0.55–0.60 F1 on the existing benchmark, but that
is a target, not a claim.

The project owner will run the existing benchmark manually after implementation.
This roadmap does not create a corpus, schedule paid runs, add a benchmark CI job,
or make benchmark execution part of implementation.

**Implementation status: Done.** The target remains unclaimed until the owner runs
the existing benchmark manually.

## Failures this work fixes

- Incremental Stage 1 correctly sees the newest delta, but the PR RAG overlay is
  also replaced from that delta. By run 4, code changed in runs 1–2 can be missing
  or resolve to target-branch content.
- Previous OPEN issues are placed in discovery prompts and unmatched issues are
  appended to later results. Historical IDs then bypass parts of fresh evidence
  validation, which perpetuates stale false positives.
- LLM planning owns the initial plan and the host repairs omissions afterward.
- Broad Stage 1 batches and broad Stage 2 prompts trade exact causal evidence for
  compressed, loosely related context.
- Stage 1 verification happens before cross-file discovery, uses iterative string
  searches, and can retain candidates when verification is inconclusive or fails.
- Later model stages can deduplicate or mutate issue membership without stronger
  source evidence.

## Target runtime

```text
newest valid delta + complete base-to-current PR source snapshot
  → mandatory hunk/source review units
  → one LLM planning annotation
  → universal Stage 1 review
  → bounded exact-evidence follow-up when needed
  → evidence-triggered cross-file investigation when needed
  → common deterministic provenance checks
  → one batched adversarial verification wave
  → exact host-fingerprint deduplication
  → all-run Java lineage matching
  → deterministic rendering
```

## Core decisions

1. **Two scopes, one request flow.** A provider-neutral, complete base-to-head path
   manifest is captured before review-scope filtering. `rawDiff` carries the
   reviewable base-to-head text and `deltaDiff` the newest valid reviewable delta.
   Delta paths control direct review; the complete manifest and exact current-head
   contents control PR RAG and context retrieval. No second analysis API or
   snapshot subsystem is introduced.

2. **Deterministic coverage plus LLM judgment.** The host assigns every reviewable
   hunk to a unit. The existing planning call adds risk focus and hypotheses but
   cannot omit units. Every unit receives the same universal correctness pass;
   lenses are additive, never exclusive routing.

3. **Exact evidence on demand.** Parser metadata and revision-bound RAG may locate
   a candidate path or symbol. Only immutable current-head source is evidence.
   Each unresolved question is routed either to a Stage 1 continuation or to a
   cross-file investigation, never both.

4. **Reuse the current candidate path.** `CodeReviewIssue` and
   `CandidateEvidenceLedger` remain the only candidate structures. Add three
   internal, non-persisted fields to `CodeReviewIssue`: `triggerCondition`,
   `causalPath`, and `observableImpact`. No `ReviewCandidate`, database table, or
   public schema version is added. After confirmation, persist only a compact
   category-independent `lineageFingerprint` on the normal issue row so later
   runs do not depend on model prose or the lossy previous-issue DTO.

5. **History follows discovery.** Historical issue prose never primes discovery.
   Only confirmed current candidates may create a new occurrence linked to an
   older active occurrence. Category and severity are not identity. Unmatched
   history is not copied into the current report.

6. **Final stages cannot invent findings.** Deterministic code validates source
   identity and exact evidence references. A one-call verifier decides semantic
   sufficiency. Only identical host-computed causal/evidence fingerprints merge;
   final rendering cannot add, remove, or rewrite issue membership.

## Cost boundary

This reassigns existing model work instead of adding open-ended agent loops. The
logical application-call shape is:

```text
1 planning call + G general-review calls + F evidence-follow-up calls
                + V verification calls + R retry attempts
```

Initial per-review limits use the existing configuration mechanism, not a new
control service:

| Work | Limit |
|---|---|
| Stage 1 input | Existing 60k-token batch target and 35k-token diff-chunk target |
| Stage 1 output | Existing 20k/30k/40k small/medium/large headroom |
| Stage 1 context | At most 2 requests/unit, 4/pack, and 1 continuation/pack |
| All evidence follow-up | At most 4 calls/review shared by local continuations and cross-file investigations |
| Cross-file admission | At most 8 coalesced tickets; 120k-character packets and 6k output |
| Verification | At most 8 candidates/call, 120k characters/call, 3k output/call, and 4 calls/review |
| Retry attempts | At most 1 provider retry for a failed planned call; no model fallback/repair calls |

`F <= 4` across both local continuations and cross-file calls. One causal question
can consume only one route. Accepted questions are coalesced by evidence component
and packed to the configured character ceiling. Later calls receive only candidate-relevant windows,
with shared source included once per pack; a host cache alone does not reduce
billed prompt tokens. `R` counts actual provider retry attempts, not logical calls.

Malformed structured output becomes an observable incomplete pack after
deterministic parsing. It does not trigger an LLM repair/fallback call. A failed
planned call may make one provider retry; another failure makes that pack
incomplete. Work beyond the per-review boundary is marked incomplete and is not
published. These limits do not change service-level concurrency or serialize
separate PR analyses.

This removes the broad unconditional Stage 2 path, iterative verifier rounds,
model semantic-dedup calls, and the authoritative Stage 3 call. Exact cost and
quality changes are determined only by the owner's later manual benchmark.

## Phases

1. [Current-head incremental context](phases/01-current-head-context.md)
2. [Hybrid review and exact evidence](phases/02-hybrid-review-evidence.md)
3. [Targeted cross-file investigation](phases/03-targeted-cross-file.md)
4. [Verification, lineage, and publication](phases/04-verification-lineage-publication.md)

## Out of scope and definition of done

There is no new corpus, automatic benchmark, quality dashboard, A/B test, canary,
shadow mode, rollout framework, or new global parallelism policy.

Implementation Done means the task-table behavior is present, the affected
Java/Python verification passes, and runtime behavior matches this design. The
root integration pass reruns the frontend and static documentation builds after
all shared edits. The owner then runs the existing benchmark manually.

Recorded implementation verification:

- inference-orchestrator unit and integration suites: **1,609 passed**;
- RAG pipeline unit suite: **1,076 passed**;
- Java changed-test reactor across **20 modules: BUILD SUCCESS**;
- web frontend production build: **passed**;
- public documentation build, 150-page prerender, and SEO verification: **passed**.

No benchmark was run as part of implementation.

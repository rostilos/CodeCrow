# Phase 3 — Targeted cross-file investigation

**Status: Done.** The active implementation is `targeted_cross_file.py`.

## Outcome

Cross-file model work investigates concrete causal interactions instead of broad
repository checklists.

## X1 — Admit only falsifiable interaction tickets

A ticket requires both:

1. a specific incompatibility or causal hypothesis tied to a changed trigger; and
2. an exact caller, implementation, override, contract-consumer, configuration,
   schema, route, persistence, authorization, transaction, state, concurrency, or
   resource-lifecycle edge.

Neither an edge nor an LLM planning hypothesis is sufficient alone. PR size, task
text, severity, and generic “related code” are never triggers. This admits small
PRs with real interactions without running broad cross-file review for every large
PR.

For a Stage 1 proposal that names a relationship but not a proven edge, run a
bounded host navigation step before admission: use enrichment/parser facts or RAG
to locate candidate paths, fetch immutable source, and confirm the reference or
contract edge with available parser/static facts. Admit a model ticket only when
that exact edge and the changed-trigger hypothesis are both present. Questions
routed here do not also receive a local continuation.

## X2 — Coalesce and investigate exact evidence

Coalesce tickets by changed trigger and connected dependency component. Use the
Phase 2 cache and include only the changed hunk, exact current-head definitions or
consumers, relationship, and falsifiable question. RAG may locate candidates; it
is not final evidence.

Admit at most eight coalesced tickets and pack them under the configured
120,000-character evidence boundary.
Local continuations and cross-file investigations together may consume at most
four follow-up calls per review. There is no iterative read loop. Unavailable or
overflow work is observable and incomplete rather than published speculatively.

## X3 — One candidate path

Emit `CodeReviewIssue` directly with the internal trigger/path/impact fields,
`relatedLocations`, and ledger receipts. This supports missing companion changes
and unchanged-consumer manifestations because the issue anchors its cause to the
changed trigger and supplies exact related evidence. Remove the separate
cross-file issue conversion model.

## X4 — One downstream order

Remove the broad duplication checklist and size/profile invocation heuristics.
The order becomes:

```text
all Stage 1 packs and local continuations
  → admitted cross-file tickets
  → common provenance gates and verification
```

Phase 3 is complete when generic PRs make no cross-file call, concrete interaction
fixtures work for PRs of any size, and every result reaches the common ledger and
verifier.

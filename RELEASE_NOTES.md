# CodeCrow 2.0.0

This release keeps one current runtime path and preserves compatibility only
when reading historical data.

## Pull-request review

- Projects can select the existing `CLASSIC` review or the new `AGENTIC`
  review; `CLASSIC` remains the default for existing configurations.
- Agentic reviews bind the webhook head to provider metadata, acquire the diff
  from the exact merge-base/head pair, and apply the existing project scope and
  size limits before analysis.
- Repository context is staged at the exact head in a bounded, read-only
  workspace and removed after processing or before dispatch when work is
  skipped.
- A model response is accepted only when reviewed and explicitly unreviewable
  work-item IDs form an exact partition of the supplied diff batch.

## Removed from the release candidate

The rollout-policy framework, shadow/candidate versions, immutable execution
manifests, coverage ledger, delivery outbox, RAG processing versions, benchmark
corpus, generated baselines, and custom offline test harness are not part of
the 2.0.0 runtime.

## Build verification

CI runs the Java reactor, inference-orchestrator tests, RAG-pipeline tests, and
frontend tests as ordinary package jobs. Deployment is gated on that test
workflow and uses the tested commit-tagged image set. Backend promotion replaces
the four backend services together and cancels queued or in-flight work from
the previous runtime before starting the new consumers.

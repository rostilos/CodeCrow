# CodeCrow PR Review Release Candidate

Status: unpromoted candidate. Production promotion requires an explicit operator decision after the exact source revision and image digests are recorded.

## Supported PR-review behavior

- GitHub, GitLab, and Bitbucket pull-request events acquire one exact base/head snapshot.
- The diff and changed-file contents are bound to an immutable execution manifest and SHA-256 digests.
- Every required diff hunk has explicit `COMPLETE`, `PARTIAL`, or `FAILED` coverage truth; incomplete zero-finding work cannot be published as clean.
- Manifest-bound reviews use deterministic local planning, Stage 1 changed-file review, and Stage 2 cross-file review for multi-file changes.
- The closed Stage 1/Stage 2 finding set passes deterministic source-evidence verification, exact duplicate removal, and deterministic summary rendering.
- Completed work is checked against the latest accepted head before persistence and delivery.
- Analysis truth is persisted in PostgreSQL and delivered through the idempotent outbox. Provider retry does not rerun analysis or create a second provider effect.

## Wire and rollout compatibility

- New PR work uses the V2 candidate envelope on `codecrow:analysis:jobs` with mandatory manifest and coverage data.
- Unversioned queue input remains only for existing branch/command compatibility; it is not a second PR-review rollout path.
- Deploy inference workers first, confirm old workers are drained, then deploy the pipeline agent. A full-stack stop/start also provides a safe boundary.
- Do not run new Java candidate producers with old Python workers. Before restoring an old inference worker, stop new PR work and drain V2 jobs.

## Database changes

Managed Flyway migrations V2.15 through V2.18 add immutable execution/input artifacts, coverage accounting, exact execution uniqueness, and durable delivery current-head/outbox state. The web server remains the sole migration owner.

## Image identity

The deployment workflow builds images under an immutable commit tag and records each registry digest. Building a candidate does not deploy it unless the manual `promote` input is enabled. Production compose requires `CODECROW_IMAGE_TAG`; mutable application `latest` tags are not accepted.

## Intentional limits

- Manifest-bound PR review does not use mutable RAG indexes or MCP file reads. It analyzes exact changed-file contents and bounded cross-file context carried by the request.
- Explorer/checkpoint recovery, evidence DAGs, extra model passes, independent model-verifier jobs, automatic canary rollback, retention/legal-hold UI, lifecycle UI, shadow comparison, and RLM/ensemble execution are not supported runtime features.
- No comparative precision, recall, false-negative, false-positive, cost, latency, or “best-in-class” claim is made without the unavailable labeled held-out corpus.
- Branch analysis and interactive commands retain their existing compatibility behavior and may still use MCP/RAG independently of the PR-review path.

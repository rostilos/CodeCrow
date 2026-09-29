# Review latency investigation — 2026-09-28

## Scope and evidence

This change starts from the user's reverted review implementation at `a4ef0e01`.
The deployed verifier, review service and agent-call source hashes matched that
baseline. Inspection used existing application logs, immutable graph receipts and
read-only job records. No services, runtime configuration, benchmark harness or
provider requests were started, rebuilt or restarted. Existing unrelated index
capacity edits were retained.

## Garden PR 7, project 1702, job 10276

Times below are UTC on 2026-09-28. The persisted job timeline separates queue
admission, graph preparation and actual model work:

| Event | Timestamp |
| --- | --- |
| Local target and proposed overlay staged | 20:07:06.657 |
| Inference queue acknowledgement recorded | 20:07:07.226 |
| ReviewService begins graph preparation | 20:15:27.780 |
| Proposed-tree graph ready | 20:17:47.424 |
| Discovery begins | 20:17:47.437 |
| Only discovery response completes | 20:17:52.641 |
| Inference completes | 20:17:52.644 |
| Java job fails refreshing VCS connection token | 20:17:54.956 |

The review waited approximately **8 minutes 20 seconds** at the ReviewService
whole-review semaphore, spent **2 minutes 20 seconds** preparing its graph, and
spent **5.2 seconds** on its only model call. Its 30-second Redis heartbeats were
liveness signals during admission, not evidence of graph or model progress.

The available garden branch generations had revision `647cf9c06c78...`; the review
captured target revision was `3849c5325046...`. The prepared graph correctly used
the captured target and proposed overlay rather than silently substituting the
other revision. However, full preparation cleared the unusable base-generation
pair while inference kept sending the original pair. Its first graph query then
rejected the just-built graph with HTTP 409, naming `base_collection_target` and
`base_generation_manifest_sha256` as incompatible provenance.

The token-refresh failure occurred after inference returned and is a separate
publication problem. The outer webhook handler marks the durable job failed;
expired credentials can also prevent replacing a VCS progress comment with a
failure comment. This patch does not alter credentials or publication semantics.

## Benchmark bottlenecks

The stable verifier runs one independent case after another. A long investigation
therefore delays every later case and holds one of the shared whole-review
admission permits until the entire PR finishes. HTTP benchmark requests and Redis
reviews share that gate, even though Redis had already accepted and acknowledged
the garden job.

Existing logs show 336 completed model responses across the inspected PR IDs.
One Discourse case produced 35 responses across roughly 21 minutes; one Cal case
held the serial verifier for roughly 16 minutes. A model response following its
last source read took roughly 7 minutes 22 seconds. All 518 logged tool calls
combined took 26.689 seconds. Source-tool latency was not the dominant cost.
These are service-lifetime observations, not a fixed-corpus comparison: old logs
lack unique run identifiers on every model line and may include duplicate runs.

The HTTP NDJSON stream emitted no idle heartbeat. Benchmark requests logged
HTTP read timeouts while waiting for admission or model completion. The Redis
queue already had a separate 30-second heartbeat; that did not protect HTTP
clients.

All 347 observed wire-capture attempts failed with `PermissionError`. The
service-owned capture root was mode 0755, while source code required 0700 even
for that empty parent directory. Consequently no current request/response bodies
were available. Earlier captures predated this service instance and were not used
to infer current prompts or provider stop reasons.

Twenty-one provider usage records reported zero input, one output token, and
34,774–134,024 reasoning tokens. Source and offline SDK serialization checks found
no review `max_tokens`/`max_completion_tokens` setting and no local counter
normalization responsible for those values. The response metadata cannot establish
truncation or accurate cost without the missing wire bodies. Model/effort settings
were deliberately preserved.

## Implemented behavior

- ReviewService no longer holds a provider admission permit for a review's entire
  lifetime. Provider calls use a request-fair round-robin scheduler. One review's
  queued batches/cases cannot monopolize the queue ahead of every later request.
- `MAX_CONCURRENT_REVIEW_CALLS` controls actual provider invocations per process;
  when unset it inherits `MAX_CONCURRENT_REVIEWS` (now default 16). Redis admission,
  graph preparation, ready-index/source reads and model calls have independent
  capacity. Pending Redis work remains durable until a worker reserves admission.
  Java index dispatch and RAG full/delta indexing also default to 16. Heavy HTTP
  preparation/index work waits outside the query thread budget. Explicit lower
  runtime overrides remain effective and must be changed at deployment.
- Manual Java review actions and repository maintenance use separate managed
  execution pools, defaulting to 16 each. MVC response streams have another pool,
  defaulting to their combined concurrency (32), so waiting response writers do
  not occupy action workers. These entry paths no longer use the common ForkJoin
  pool. Additional request/stream submissions wait in their queues instead of
  being rejected when the running workers are occupied. The configured MVC async timeout is honored instead of imposing a hidden
  five-minute timeout. Webhook admission already expands to 20 and retains its
  existing durable backlog.
- The local benchmark harness keeps its default 10 parallel PR pipelines.
  Unset `--index-jobs` now inherits `--parallel-jobs` instead of serializing
  indexing at one worker. The host-wide cohort lock is opt-in; explicit
  environment/CLI locks remain available. The Magento index-only worker override
  was removed. Same-project mutation and duplicate-index single-flight behavior
  remain intact. These changes are in sibling `../benchmark/codecrow_crb_harness.py`,
  outside this Git repository; no benchmark was executed.
- Independent existing verification cases and read-only tool groups overlap.
  Each case retains its original native conversation, source, reasoning settings,
  evidence numbering and decisions. Decision tools are ordering barriers. Final
  aggregation and semantic reconciliation keep original case order.
- Planner graph fetches overlap without changing ownership, grouping, graph
  coverage, pagination or diagnostic ordering. Concurrent identical source reads
  share a request-local operation; graph focus remains part of cache identity.
  Failed/partial reads stay retryable. Cancellation joins child work and releases
  permits rather than leaving paid calls behind. Case start and completion
  events are separate; completed counts reflect actual returns, including
  partial fallback outcomes, with metadata-only case timing logs.
- NDJSON streams emit an idle heartbeat every 30 seconds without claiming stage
  progress or delaying ordinary/terminal events.
- Graph preparation returns its actual sealed base-generation pair, including
  explicit nulls when an unusable seed requires full preparation. Inference adopts that pair for subsequent
  queries. Tenant, branch, revision, manifest and source checks remain enforced.
  The host prefers an exact target generation, then an active same-project/branch
  seed. `ragBaseGenerationRevision` identifies that storage seed independently of
  the review target. Sealed per-file identities determine every changed/deleted
  selected path when revisions differ; unchanged graph content is reused. Tests
  compare its complete graph against a separate full build. Missing compatible
  identities still use an observable exact fallback, and later requests reuse the
  sealed result. This does not substitute a different target revision.
- Debug capture accepts a service-owned root with read/traverse permissions for
  others, but rejects group/world-writable, foreign-owned or symlink roots.
  Tenant/run directories remain 0700 and artifact files remain 0600. Failure stays
  observable and optional. Queue wait and provider execution have separate logs.

## Validation and limits

Offline replay checks compare serial and concurrent native/JSON case messages,
model options, schemas, evidence/decision order and final reconciliation output.
They establish execution equivalence for those fixtures, not F1 equivalence on
real models. Tests also exercise fair scheduling, capacity limits, cancellation
races, idle stream/disconnect behavior, request/source isolation, retryable reads,
and real SQLite graph preparation/query round trips with both exact and stale
base receipts. Public Docs describe the implemented native verifier and scheduling
rather than the reverted phase-based design.

Scheduling does not reduce review stage call counts, token budgets or source
coverage. Concurrent case transcripts can use more memory than serial verification.
Physical throughput depends on provider capacity and CPU/RAM; sixteen concurrent
operations does not establish a sixteen-fold speedup. The live environment still
contains `MAX_CONCURRENT_REVIEWS=4`; source defaults do not override explicit
operator configuration. It must be set to at least 16 when the user applies this
change. No mounted runtime files were changed.

Code verification: the complete offline inference suite passed **885 tests**
(with two third-party warnings), including isolated installed-SDK protocol checks.
The complete offline RAG suite passed **402 tests**. Real graph delta/full-build
oracles cover changes outside the PR overlay and missing-hash fallback. Java
seed-handoff tests passed **22 tests**, and repository-index executor tests passed
**4 tests**. Java action/stream admission, controller wiring and webhook recovery
tests passed **12 tests**, including simultaneous 16-review, 16-maintenance and
32-stream execution plus queued overflow. Ten focused offline benchmark-harness
tests passed, covering inherited/explicit capacity, overlapping default cohorts,
explicit lock behavior, Magento worker parallelism, same-project serialization,
index single-flight and persisted corpus order.

The final isolated public Docs `npm run build` passed with **150 canonical pages**
and **6,270 root-relative anchors**. Explicit route/fragment checks covered 26 links
across 18 touched Docs files with no failures. Source and Docs whitespace checks
passed; the original Docs build output was not replaced.

No paired benchmark or judge was run, so this report makes no measured post-change
F1, token-cost or end-to-end speed claim. Runtime behavior changes only after the
user's later service rebuild/deployment. The existing long model turns remain a
material latency limit to measure using the repaired capture and timing logs.


## Provider protocol and routing investigation (follow-up)

Primary documentation and actual offline SDK serialization exposed a concrete
adapter bug: OpenRouter returned reasoning fields, but LangChain's default
ChatOpenAI conversion discarded them both on decode and when serializing the next
native assistant message. The adapter now preserves the original reasoning state,
including structured identifiers/signatures and ordered streaming fragments.
This is a protocol correction, not measured proof that it explains every long turn.

- [OpenRouter reasoning continuity](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens#preserving-reasoning)
  requires original reasoning details across tool turns.
- [DeepSeek thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/)
  requires prior reasoning content in tool conversations.
- [Cloudflare's model description](https://developers.cloudflare.com/workers-ai/models/deepseek-v4-flash-0731/)
  maps medium reasoning to high for this model. The configured review effort is
  preserved rather than reducing thinking as a latency workaround.
- [DeepSeek queue keepalives](https://api-docs.deepseek.com/quick_start/rate_limit/)
  explain why socket inactivity timeout alone cannot bound queue delay: empty
  lines/SSE comments can keep a request open while waiting to start.
- [OpenRouter streaming](https://openrouter.ai/docs/api/reference/streaming)
  documents cancellation support for Cloudflare/DeepSeek, unlike non-streaming
  requests that may continue inference and billing after the client disconnects.

Review calls internally aggregate the full OpenRouter stream into the ordinary
final message. Cancellation closes the stream; actual reasoning/content/tool
progress is measured without logging private reasoning text. A configurable
elapsed deadline (`REVIEW_MODEL_CALL_TIMEOUT_SECONDS`, default 900 seconds) is a
failure boundary, not the latency optimization. It starts only after admission;
partially consumed streams are not silently replayed as extra paid calls.

Default unpinned OpenRouter routing now prefers throughput for the same model;
explicit provider order, sort, only, privacy and other routing constraints remain
authoritative. This avoids relying on price-based routing for long reasoning
conversations. See [provider routing](https://openrouter.ai/docs/guides/routing/provider-selection).
The user identified Cloudflare as the backend used, but the stopped benchmark's
explicit routing constraints could not be reconstructed from its failed captures.
No claim of a verified Cloudflare outage is made.

# Paid work and external side effects

These rules supplement the workspace engineering instructions. Financial safety
is a correctness requirement. The November 2026 QA replay incident caused real
financial harm; unrequested repeated spending is a critical defect, not a minor
reliability issue. Do not claim a prompt or a passing happy-path test prevents it.

## Authorization and terminal state

- A deployment, restart, migration, database restore, stale branch, or failed
  operation is not new authorization to spend money. Trace each paid invocation
  to an accepted user/provider event and its durable execution receipt.
- Never automatically resurrect FAILED, CANCELLED, SKIPPED, or COMPLETED work.
  An explicit authorized retry must be distinguishable from automatic recovery.
- Recovery of accepted PENDING/QUEUED work must claim it atomically. Recovery of
  RUNNING work must prove the previous execution no longer owns it and reconcile
  completed side effects. A lease expiry or old timestamp alone does not prove
  an external operation stopped. When the outcome is ambiguous and cannot be
  reconciled, record an explicit failure instead of submitting it again.
- Scope identities, receipts, idempotency keys, budgets, and cached results to the
  tenant/project and exact operation/input. Never deduplicate across tenants.

## Retries and cancellation

- A timeout, disconnect, missing acknowledgement, 5xx, or lost result does not
  prove the provider did no work. Do not wrap paid generation or non-idempotent
  external writes in generic transport retries. Reuse a durable result or a
  provider-supported idempotency mechanism; otherwise surface the uncertain
  outcome for an explicit retry. Keep safe read retries separate from writes.
- Count attempts at every layer: scheduler, queue, HTTP client, provider SDK,
  agent, JSON repair, and fallback. Nested finite retry counts multiply. A new
  worker, fallback, or model wrapper must not silently reset the allowance.
- Propagate cancellation through the caller, queued/running worker, child tasks,
  model requests, and MCP/tool sessions. Stop launching new calls after loss of
  execution ownership. Record that already accepted provider calls may still be
  charged; cancelling only a database row is not cancellation of execution.

## Bound actual spending

- A model context window, per-call token cap, semaphore, recursion limit, or
  per-agent step limit is not a job-wide spending limit. Calculate the total
  request fan-out, including batches, semantic shards, enrichment, verification,
  repairs, fallbacks, and SDK retries before introducing or changing paid work.
- Enforce any authorized job allowance in host code at the actual invocation
  boundary, shared across concurrent tasks and preserved across recovery. The
  model must not be trusted to police spending through natural-language prompts.
  Exhaustion must stop further paid calls and expose partial results or a clear
  terminal error. Never silently expand the allowance to make a test pass.
- Reuse the project's input scope and generated/vendor-file rules consistently
  across review, commands, QA, and fallback paths. Do not silently bypass them
  by forwarding an unfiltered raw diff. Report omitted inputs honestly.
- Do not make live paid model calls in tests or benchmarks without explicit
  authorization covering the run and its spending bound. Mock failure modes.

## Deployment and evidence

- Production-to-staging restores must neutralize copied pending jobs, intents,
  retry state, and outbound credentials before application startup. Preserve
  history. Do not wipe production or disable legitimate recovery as a shortcut.
- Before editing, enumerate every caller, producer, consumer, retry layer,
  scheduler, and fallback for the affected operation. Inspect its lifecycle end
  to end. Reuse existing mechanisms rather than adding another competing queue.
- For changes affecting paid work or external side effects, add regressions for
  the applicable failure paths: duplicate delivery, concurrent workers, success
  with a lost response, timeout while the worker continues, cancellation, restart,
  restored history, retry exhaustion, and multiplicative fan-out. Assert the
  number of external calls and resulting terminal state, not just eventual success.
- Document what is enforced versus merely observed. Record unresolved financial
  risks and untested boundaries explicitly. No claim of "cannot happen again"
  without evidence; instructions and review prompts are supplementary controls.

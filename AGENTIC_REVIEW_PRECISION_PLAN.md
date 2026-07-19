# CodeCrow review quality plan

## Goal

Improve precision, recall, and F1 without making review cost or latency
unacceptable. The pre-improvement baseline (26.2% precision, 44.9% recall,
33.1% F1 on 50 golden PRs) is not acceptable for customer rollout.

The implementation must stay general. It must not contain rules written for a
particular benchmark PR, language, framework, repository, or defect.

## Two comparable review approaches

Keep both approaches selectable per project and in the benchmark harness:

- `CLASSIC`: the existing staged analysis with orchestrator-managed RAG
  enrichment.
- `AGENTIC`: one repository-aware review pass over the immutable PR diff, with
  optional local repository and RAG MCP tools.

Both approaches use the same PR input and customer-facing issue format. There
is no silent fallback between them, so their quality, cost, and duration can be
measured independently.

## Agentic flow

```text
immutable head SHA + raw PR diff + project context
                     |
              exact-head workspace
                     |
       one bounded repository-aware LLM loop
          |                         |
    local code tools          RAG MCP tools
          |                         |
          +-----------+-------------+
                      |
             structured findings
                      |
        diff anchoring + cheap deduplication
                      |
              published PR issues
                      |
             delete the workspace
```

The model receives every changed hunk in a deterministic worklist. It can use
`read_file`, `search_text`, `find_symbol`, `read_diff_hunk`, and targeted RAG
tools when additional repository context is useful. Tool use is optional: a
local defect should not require redundant reads, and a cross-file claim should
be explored when the diff alone is insufficient.

The model returns normal structured findings:

- defect or advisory;
- confirmed, rejected, or inconclusive;
- severity and category;
- file, line, and exact code snippet;
- title, reason, and suggested fix.

There is no model-facing evidence-receipt language, mandatory causal taxonomy,
category-specific playbook, or correction loop. Tool receipts remain internal
telemetry and are not required fields in a finding.

## Publication boundary

Publication performs only inexpensive checks that the service can determine
reliably:

1. The result is a confirmed defect, not advice or an inconclusive hypothesis.
2. Its severity represents a customer-visible defect.
3. Its file and non-empty line occur on the new side of a supplied PR hunk.
   Added and unchanged context lines are both valid anchors, and the published
   snippet is normalized from that immutable diff line.
4. If the line hint is inaccurate, use exact lines from a single- or multi-line
   model snippet to locate the nearest visible line in that file's hunks.
5. Remove an exact duplicate with the same file, normalized line, and title.

Do not ask the LLM to repeat a tool receipt, create a five-part evidence chain,
or retry its answer merely to satisfy publication bookkeeping. Those steps add
tokens and failure modes without proving that the semantic conclusion is true.

Coverage failures remain separate from model quality. An incomplete review is
an infrastructure failure and is not scored. A complete review that publishes
zero findings is a valid pipeline result and its missed golden issues count as
false negatives.

Immutable non-reviewable anchors count as accounted coverage: deleted files use
`DELETED_RECORDED`, while binary and rename-only file sections use
`UNSUPPORTED`. They have no reviewable new-side text hunk. A PR with every text
hunk examined must not become partial merely because it also contains one of
these terminal dispositions. `FAILED`, `INCOMPLETE`, `PENDING`, and
`OWNER_PENDING` remain non-complete states.

A structurally valid final response completes every hunk in its supplied batch
unless the model explicitly marks a hunk unreviewable. Echoing opaque hunk IDs
in a separate reviewed list is optional accounting, not proof of attention and
not a reason to fail an otherwise completed review. A finding may publish from
any exact line in the immutable PR worklist, including a related hunk discovered
while exploring repository context from another batch.

Parse the model boundary item by item. Provider wrappers, extra envelope fields,
or one malformed finding must not discard the other valid findings or coverage
for the entire batch. Normalize common casing and enum aliases, retain valid
items, and expose rejected-item and failed-batch reason counts in diagnostics.

## Context improvements to evaluate

The agent should receive the context that changes its conclusion, not a large
automatic RAG dump:

- exact PR title, description, task context, project rules, and changed hunks;
- current definitions, callers, consumers, configuration, and related tests
  discovered through local tools;
- targeted RAG search for project-specific behavior or relationships that are
  difficult to locate lexically;
- previous open findings only when reconciliation is requested.

Measure which tools contribute to true positives. If a RAG query does not
change findings or rejection decisions, remove or retune it rather than adding
more retrieved text to every prompt.

## Cost controls

- Keep work in source-diff order and cap a batch at 16,000 characters. This
  avoids a single mixed 40+ hunk prompt while still reviewing related nearby
  hunks together.
- Limit repository exploration to eight tool-enabled model rounds. If the
  model reaches that limit, make one tool-free structured finalization call so
  already-reviewed hunks and findings are not discarded.
- Do not make correction-only model calls.
- Prefer exact local search/read tools before semantic retrieval when the model
  knows a path or symbol.
- Record model tokens, local calls, RAG calls, latency, and cost per PR and per
  published true/false positive.

These are budgets, not quality policies. Tune them from benchmark results and
large-PR reliability data.

## Repository security and cleanup

The workspace is downloaded by immutable head SHA, extracted without executing
repository code, and exposed through read-only bounded tools. Archive extraction
rejects path traversal, links, special files, and configured size/file-count
limits. An individual regular file above the per-file tool limit is skipped
without being decompressed or written, so a legitimate large binary asset does
not abort review of the remaining repository. Skipped entry counts, bytes, and
bounded paths remain visible in agentic diagnostics. The model receives no
shell, credentials, write tools, or arbitrary network access.

The archive is deleted after extraction and the workspace is deleted in a
`finally` path after success, failure, timeout, or cancellation. Startup/TTL
cleanup handles a crashed worker. Cleanup completion remains a required
infrastructure diagnostic for an agentic benchmark run.

## Evaluation

Run `CLASSIC` and `AGENTIC` on the same frozen 50-PR set using the same model and
judge configuration. Capture at least:

- precision, recall, F1, TP, FP, and FN;
- results by severity/category and by repository;
- zero-finding PRs and filtered-finding reasons;
- model tokens/cost, local and RAG tool calls, duration, and failed reviews;
- true positives retained versus the classic baseline.

Initial rollout targets:

- precision at least 50%;
- recall no worse than the agreed baseline retention threshold;
- F1 materially above the 33.1% baseline;
- cost and p95 latency within the product budget;
- no incomplete review scored as a false negative;
- no persisted agentic archive/workspace after completion.

Do not tune against one golden PR. Use single-PR runs only as publication and
infrastructure smoke tests; make quality decisions from the complete paired set
and then confirm them on a holdout set.

## Current branch status

Implemented:

- selectable `CLASSIC` and `AGENTIC` paths;
- immutable exact-head workspace and cleanup lifecycle;
- local repository tools plus RAG as MCP tools;
- deterministic diff worklist and coverage diagnostics;
- lean agentic finding schema;
- source-order 16,000-character batches and bounded source-read guidance;
- one tool-free finalization when exploration consumes its round budget;
- batch-level completion without mandatory work-item ID echoing or same-batch
  publication filtering;
- tolerant batch-envelope parsing and independent finding validation;
- visible-hunk anchoring, unique-snippet line normalization, and cheap dedup;
- raw benchmark response/diagnostic capture;
- independent benchmark commands for old and new pipelines, with resumable or
  one-based restart controls to avoid paying twice for completed PRs; a selected
  rerun invalidates its old result before work starts so failures cannot be
  scored from stale output.

Removed from the agentic runtime path:

- mandatory evidence chains and receipt reproduction;
- root-symbol/failure-path output taxonomy;
- category/language/framework-specific proof scripts;
- publication correction retries;
- the benchmark invariant that aborted a complete run when a confirmed model
  finding was filtered.

The remaining decision is empirical: rebuild the service, run the smoke test to
verify end-to-end publication, then run all 50 PRs and compare quality and cost.

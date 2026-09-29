# Graph-first MCP review implementation and evaluation

Selected state: `CodeCrow-MCP-GraphFirst-20-20260929-03`.
On 2026-09-29 the user requested an exact source restoration to this benchmark
and explicitly prohibited redeployment. The six differing runtime files were
copied from the preserved run03 image
`sha256:da0eaa2f4dd26df75f6b01e4d8fab712355c184724b825ddd345d4b6850a3d08`.
All 91 Python runtime files match that image byte for byte, including all 21
files in the recorded run03 hash manifest. Later tests and documentation were
reversed using recorded edits. No service was started or redeployed.

Run03 completed all 20 paid reviews at `LLM_TEMPERATURE=0.6`; all 20 have valid
extraction, deduplication and judge results. On the 19 PRs with historical labels,
core F1 was 53.4%, versus 47.4% for the baseline. Eight reviews were complete and
12 partial; the quality and completion limitations below remain applicable.

## Implemented workflow

Set `REVIEW_EXECUTION_MODE=mcp_only` on inference, or send internal
`reviewExecutionMode: "mcp_only"`. The default remains `pipeline`; invalid values
fall back with a diagnostic. `analysisMode` continues to select full/incremental
work, and `RAG_ENABLED` independently controls graph availability.

1. The host parses complete active hunks and prepares an optional pinned graph.
   It does not select graph relations or owner source for an MCP-mode prompt.
2. An agent plans from file paths, hunk counts and change sizes. Its MCP inventory
   contains compact graph navigation and paged change metadata, without source or
   diff reads. It groups related changes into focused tasks.
3. The host recovers omitted paths and packs tasks at complete hunk boundaries
   using a 24,000-character diff-size target. A larger individual hunk remains
   whole; nothing is truncated. Every active hunk retains one analysis owner.
4. Analysis sessions receive only their scope and change metadata. They fetch
   complete diffs, graph metadata, definitions and local source themselves. A
   hunk cannot count as reviewed before its diff was actually returned by a tool.
   Identical repeated results can reference earlier delivered evidence instead
   of resending source bodies. Changing prose alone does not keep a stalled
   conversation alive. `recordReviewDecisions` checkpoints discovered findings;
   final task output accounts for reviewed hunk IDs and summaries.
5. When multiple tasks exist, a tool-enabled pass checks remaining cross-task
   contracts using compact summaries and exact evidence where needed.
6. Candidates and concrete questions enter the existing independent verifier,
   whose initial source is also fetched on demand in this mode. Source-grounded
   verdicts, active anchors, partial fallback and final duplicate reconciliation
   retain the existing contracts. There are no verification cases when analysis
   has no candidates or concrete questions.

The graph remains hosted by the current structural-index service. It already
uses SQLite structural generations without embeddings/vector retrieval. MCP
exposes that graph and local staged source; it does not remove graph construction
or storage. No language/framework implementation was added to a generic host.

Graph outages retain diff/read/search tools inside the selected mode. Failed
planning recovers file tasks. Interrupted analysis retains recorded candidates
and marks incomplete work partial. Provider failure does not replay the request
through another workflow. Tenant, revision, path and symlink isolation stay in the
shared tools. Request/result contracts and the existing queue are not versioned.

Implementation owners:

- `python-ecosystem/inference-orchestrator/src/service/review/execution_mode.py`
- `python-ecosystem/inference-orchestrator/src/service/review/mcp_review.py`
- `python-ecosystem/inference-orchestrator/src/service/review/mcp_prompts.py`
- `python-ecosystem/inference-orchestrator/src/service/review/tool_conversation.py`
- Existing `review_service.py`, `verification_tools.py`, `verifier.py` and DTO.

The shared inventory adds `listReviewChanges(paths, cursor, maxFiles)` for paged
hunk IDs, anchor ranges, source side and active/context-only markers, without
source bodies. Existing graph navigation, structural reads, literal grep and
complete diff/source tools retain their host-owned bindings.

## Model routing and paid response evidence

The required review/judge model is `deepseek/deepseek-v4-flash-0731` through
OpenRouter. The benchmark runner leaves `aiCustomParameters` absent and sets
`MARTIAN_PROVIDER=''` so judge scripts cannot inherit their local Wafer default.
The application no longer inserts `provider.sort=throughput` when custom routing
is absent. Explicit user-configured routing remains respected.

The initial Cloudflare-only requirement was clarified after live run01 responses
reported Cloudflare, Relace and Sail Research. Requests contained the exact required
model and no provider object. The user explicitly answered: "Continue automatic
routing, including Relace." This authorizes the guide's automatic route for review
and evaluation; do not restore a Wafer pin or add custom routing parameters.

Run01 was cancelled before any PR completed; it is not a scored arm. Its captures
remain under the inference log volume, with provider/usage receipts and a
conversation audit saved under `run01/`. Cloudflare responses report BYOK, so a
zero OpenRouter `cost` is not proof of zero upstream cost. Interrupted attempts
without a returned usage receipt remain gaps in billing evidence.

The paid attempt also exposed a parser defect: all four planners returned valid
fenced JSON with explanatory prose, which the host discarded and replaced with
per-file tasks. The shared parser now accepts explicit JSON fences surrounded by
text, retaining the final valid fenced value without another model call. It does
not reconstruct malformed output from inner braces. Native and JSON conversation
fixtures preserve a two-file group in one task. Run02 uses a distinct output/tool
name and the same fixed 20-PR corpus. No quality improvement is claimed from this
parser check alone.

Run02 was also interrupted before scoring: two Cloudflare planning streams
produced over 169,000 reasoning characters each without an answer or tool call.
The final 8,192-character windows had exact periods of 285 and 203 characters,
respectively. These were concrete repetitions, not a diagnosis based on elapsed
time. Run03 tests the existing `LLM_TEMPERATURE=0.6` setting while retaining the
same model, automatic routing and absent custom provider parameters. Its first
four plans completed, including the two formerly looping cases; this observation
does not establish a general loop fix or a quality improvement. The original
local temperature setting is preserved in `temperature-override.json`.

The later format-correction, planning-prompt, checkpoint-handoff and
`recordReviewProgress` changes are not part of this restored state. Run03 keeps
its original conversation termination and partial-result behavior. Subsequent
experiment artifacts remain available, but their code is not active in this
checkout.

The benchmark implementation must still record actual serving-provider evidence
for review, extraction, deduplication and judging. Configuration intent is not
proof of actual routing.

Review wire capture is already enabled. The local evaluation checkout now accepts
`CRB_LLM_LEDGER_PATH`, configured by the runner, to append receipts for each received
extraction/deduplication/judging response before parsing its answer. Receipts retain
stage, attempt, prompt hash, generation ID, returned model/provider and usage/cost;
they contain no prompts, answers, headers or credentials and add no routing
parameters. Missing providers remain unknown. Capture failures warn without
repeating paid requests. SDK-internal retries and transport-lost responses remain
a billing-evidence limitation.

## Deployment and volume reset

The preserved run03 image is the source authority for this restoration; this
request changes repository files only. The default execution mode remains
`pipeline`; run03 explicitly requested `mcp_only`. Its local temperature setting
is `0.6`, with the required DeepSeek model and absent provider overrides recorded
in the benchmark runner. Existing services and Docker image tags are unchanged.

Before preparing run01, only the named Docker volume `structural_index_data` was
cleared. Its owning RAG service was stopped first; a helper mounted only that
volume and removed its contents, then the service restarted. The reset receipt
records zero remaining entries. About 344 GB was freed. PostgreSQL, Redis, source
snapshots, logs and benchmark outputs were not cleared. The analysis queue was
empty and no benchmark process was running before the reset.

The first 20 fixed corpus PRs then completed index-only preparation with four
workers and no model review. This is preparation for the same run01 experiment,
not a scored review arm. Existing acquisition receipts/worktrees under
`/tmp/codecrow-crb` were reused; missing structural generations were rebuilt.

## Verification completed

- Full inference suite at run03: **917 passed**, with two existing dependency warnings.
  Includes native provider adapters, graph/source isolation, pipeline regression
  checks and the new MCP mode.
- Harness checks: **210 passed**, including the full harness and judge-reducer suites.
- Local offline evaluator suite: **41 passed**. Mocked HTTP checks exercise all
  three unpinned clients, returned provider/cost receipts, malformed answers and
  receipt-write failure without paid retries. An older extractor test was updated
  to the existing incremental batch iterator contract; production batching did not
  change.
- A scripted 300-file review exercised the real workflow, fetched focused diffs
  through tools, and accounted for all 300 hunks without preloading full diff or
  source. A separate oversized-hunk fixture retained its complete contents.
- Tests cover graph-unavailable native/JSON execution, independent verification
  without preloaded evidence, planner omissions, incremental inventory markers,
  candidate preservation after interruption, and termination without new facts.
- A live HTTP request with a no-change diff and a dummy key confirmed the deployed
  `mcp_only` contract without constructing a paid model request.
- `npm run build` passed in an isolated copy of the sibling Docs checkout:
  150 canonical pages and 6,279 root-relative anchors. Eleven internal links and
  the new section fragment across changed developer pages were checked with no
  broken targets. The original built site was not overwritten.
- `git diff --check` passed in the application repository.

These are architecture/contract checks, not measured review quality. No new Java
or plugin behavior was introduced. Existing Java/provider acquisition and queue
recovery logic was retained; live webhook/restart compatibility and paid verdict
quality have not been established by these offline fixtures.

## Scoring recovery

All 20 run03 extractions completed after one timed-out extraction was resumed
without repeating the other 19. Deduplication completed without fallbacks. The
initial judging pass exposed an existing wrapper cap: `CRB_REVIEW_TIMEOUT=7200`
was reduced to 300 seconds, causing two whole-PR evaluations to time out. The
wrapper now honors the configured whole-review timeout; its default remains 300
seconds and the separate per-call bound remains in place. Matching rules, model,
temperature and provider routing are unchanged. All 210 harness/reducer tests and
the Docs build passed. Both missing evaluations completed after resuming with this change.
Received pair-response receipts remain in the ledger; requests cancelled before
returning a receipt leave billing gaps. Missing, skipped and errored evaluations
must not be counted as usable labels in the paired comparison.

## First paired quality checkpoint

Run03 has 20 valid evaluations and no remaining skipped/error entries. Nineteen
have historical `CodeCrow-MCP-wo-RAG` labels; Discourse Graphite PR 4 is new-only.
The table uses precisely those same 19 PRs and frozen category definitions:

| Profile | Historical TP/FP/FN | Run03 TP/FP/FN | Historical F1 | Run03 F1 |
| --- | --- | --- | --- | --- |
| Strict | 26 / 20 / 38 | 38 / 40 / 26 | 47.3% | 53.5% |
| Core | 27 / 20 / 40 | 39 / 40 / 28 | 47.4% | 53.4% |
| All labels | 28 / 20 / 46 | 43 / 40 / 31 | 45.9% | 54.8% |

Core precision is 57.4% → 49.4%; core recall is 40.3% → 58.2%. This arm improves
measured recall/F1 with more unmatched findings. A benchmark-unmatched finding
is not automatically a disproven defect: source audits must distinguish a real
novel issue from speculative intent or a judge mismatch. The new-only twentieth
PR is excluded from the paired table. Across all 20, core TP/FP/FN is 41/45/34,
precision 47.7%, recall 54.7%, F1 50.9%.

Completion remains a separate weakness: only 8/20 reviews completed every stage,
and 486/564 hunks were acknowledged. Partial reviews' published findings are
included in scoring; their unresolved work is not treated as a clean result.
These cases form a development checkpoint, not independent held-out evidence.
Historical labels were reused, not rejudged, and original serving providers and
review settings are not established by the historical directory name.

All 2,249 received review calls used the required model and temperature 0.6 with
no provider parameter. Their receipts total 45,329,367 prompt tokens (34,208,725
cached), 2,227,062 completion tokens and 1,721,064 reported reasoning tokens.
The receipt-based billing proxy is $5.23079 for reviews: OpenRouter cost plus
upstream cost only for BYOK responses. Evaluation has 580 received receipts,
197,914 prompt tokens, 220,435 completion tokens and a $0.27980 proxy, including
received work from timeout/recovery attempts. Missing responses and hidden SDK
retries remain billing gaps; these totals are not an invoice. Providers sometimes
report reasoning-token counts inconsistent with completion-token totals, so the
fields are retained as received rather than summed into a fabricated total.

The initial-payload audit covered 225 planning/analysis/cross/verification starts:
zero contained source/diff/evidence bodies. Graph navigation still produced some
large subsequent observations. Initial payload economy alone does not establish
low end-to-end token use. This restoration selects run03 without additional
implementation experiments or paid benchmark runs.

A selected source audit found concrete mechanisms behind several unmatched
Cal.com 10967 findings: video credential loss before cancellation, a lost primary
calendar fallback, and an optional push into a null destination list. It also
identified speculative intent in two Discourse 6 domain-display findings. These
are selected source inspections, not integration tests or manual relabeling;
golden labels and reported scores remain unchanged. See
`run03/selected-source-audit.json`.

The native-tool audit counted 3,784 unique receipts visible in later model
requests: 265/282 ready `queryCodeGraph` results and 1,758/1,771 ready local
source reads, alongside ambiguous, missing and failed observations. This proves
actual graph/source retrieval in the inspected trace, not correctness of every
observation. JSON fallback and final undelivered receipts are excluded.

Saved evidence: `run03/quality-comparison.json`, `run03/run-summary.json`,
`run03/evaluation-summary.json`, `run03/payload-audit.json` and
`run03/score-state.json` under the artifact root below.

## Retained benchmark evidence

Artifacts: `/var/www/html/persisted-beches/mcp-only-20260929/`.

- `baseline/`: retained historical evaluations, candidates, dedup groups and hashes.
- `baseline/first-20-metrics.json`: exact selected-subset historical counts, with
  the category lookup and dashboard identity retained alongside it.
- `run01/corpus-20.json`: fixed selected URLs and golden-comment hashes.
- `run01/index.log`: all 20 index-only completions; exit status zero.
- `run01/index-command.json`: source-free command/model/mode configuration.
- `implementation-state.json`: image identity and outstanding paid-run constraint.
- Saved JUnit, Docs build, volume reset and live contract-smoke evidence.

The selected historical `CodeCrow-MCP-wo-RAG` dashboard covers 39 PRs: strict F1
49.7%, core F1 50.2%, all-label F1 48.1%. Its all-label counts are TP=52, FP=35,
FN=77. These are historical scores, not a paired comparison with the new code.
Recompute the baseline on the exact overlapping PRs for the first-20 checkpoint,
and preserve category definitions and source/provenance limitations.

The selected first 20 have **19** usable historical baseline evaluations; Discourse
Graphite PR 4 is missing. Recomputed on those 19: strict TP=26/FP=20/FN=38, F1=47.3%;
core TP=27/FP=20/FN=40, F1=47.4%; all TP=28/FP=20/FN=46, F1=45.9%. The initial paired
comparison must use these same 19 URLs and report the new-only twentieth separately.
There are no unresolved golden categories in this subset. Historical artifacts
were not rejudged, and their model-directory name does not establish identical
serving providers or original review settings.

The prepared local runner is `/tmp/codecrow-mcp-benchmark-run.py`. It reads only
benchmark credentials from the supplied guide, explicitly configures the required
model for review and judge, omits custom provider parameters, and uses new output
and tool names. It does not call the older wrapper that can inject Wafer.
The current authorized run03 runner is `/tmp/codecrow-mcp-benchmark-run03.py`:

```bash
python3 /tmp/codecrow-mcp-benchmark-run03.py review --limit 20 --jobs 4
python3 /tmp/codecrow-mcp-benchmark-run03.py score --limit 20 --jobs 4
```

Use `--resume` only for this unchanged configuration; the runner adds the relevant
review resume or judge reuse option. Inspect the actual process/session handle
and output before resuming an interrupted invocation. The root harness's new
`--review-execution-mode mcp_only` selector must be confirmed in the terminal
result; ignored flags cannot label a different paid arm.

The user selected the completed run03 checkpoint and ended the subsequent
implementation experiments. Restoration verification and file provenance are
recorded in `/var/www/html/codecrow-old/restore-evidence-run03-20260929/`.
The best possible F1 is not established by this finite benchmark.

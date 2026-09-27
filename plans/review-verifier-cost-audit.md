# Review verifier cost and evidence audit — 2026-09-25

## Scope and provenance

This investigation inspected existing benchmark artifacts and running services
read-only. It did not start a benchmark, make a paid model call, rebuild or
redeploy a service, restart a container, or modify live repository/index data.

The inspected inference container started at `2026-09-25T08:10:22.988232544Z`,
image `sha256:8dba87002338485db663bb8d2f8160e1e55c8fd65cc723f31beb1d8e78d0ea9e`.
Its `service/review/{verifier,verification_tools,planner,model_calls}.py` files
were byte-identical to the worktree at the start of this investigation. The
following observations concern that deployed implementation, before this
task's corrections.

Authoritative inputs:

- `docker logs codecrow-inference-orchestrator`: model responses from
  **2026-09-25 08:16:10.025 through 09:09:17.685 UTC**, inclusive.
- `docker logs codecrow-rag-pipeline`: existing graph request access logs.
- `/tmp/codecrow-crb/responses/CodeCrow-Graph-Calls-Runner-10-2-luna6/`:
  earlier response artifacts.
- `/tmp/codecrow-crb/responses/CodeCrow-Graph-Calls-Runner-10-2-luna6-mcp/`:
  six completed newer response artifacts at inspection time.
- `/var/www/html/persisted-beches/code-review-benchmark-full/offline/results/openai_gpt-6-luna/evaluations.json`:
  existing judge classifications for both arms.
- `/tmp/codecrow-graph-calls-runner-10.log`: retained review diagnostics.

The matched completed PRs were cal.com **8087, 22345** and Discourse **1, 2, 3,
5**. Response bindings identify matching base/head revisions between arms.
Other requests were present in runtime logs but had not produced completed
response artifacts in the inspected newer directory, so their usage is excluded
from the matched table below.

## Observed model usage

The table sums `usage_metadata` in existing model-response logs for those six
completed matched PRs. Output includes provider-reported reasoning tokens;
cache-read tokens are a subset of input tokens.

| Stage | Calls | Input tokens | Output tokens | Cache-read input |
| --- | ---: | ---: | ---: | ---: |
| discovery | 50 | 300,151 | 228,875 | 16,896 |
| cross_file | 6 | 90,169 | 54,936 | 0 |
| verification_validate | 262 | 3,297,841 | 477,061 | 1,811,200 |
| verification_deduplicate | 19 | 147,760 | 59,121 | 48,128 |
| **Total** | **337** | **3,835,921** | **819,993** | **1,876,224** |

Verification accounts for **281/337 calls (83.4%)** and
**3,445,601/3,835,921 input tokens (89.8%)**. PR8087 alone produced 60 validation
calls and 1,454,770 validation input tokens. Across all requests in the inspected
log interval, validation produced 447 calls and 5,353,044 input tokens; discovery
produced 99 calls and 587,348 input tokens. PR79265 validation inputs rose to
approximately 30,000 tokens per call.

These totals establish where the newer implementation spent tokens. The retained
earlier response artifacts and runner log do not contain comparable provider
billing totals, so this audit does **not** independently establish the reported
3–5x dollar ratio. Cache discounts, provider routing, output reasoning, and
incomplete requests prevent treating raw input-token ratios as dollar ratios.

## Existing paired judge results

| Matched PR | Earlier TP / FP / FN | Newer TP / FP / FN |
| --- | --- | --- |
| cal.com 8087 | 2 / 1 / 0 | 2 / 6 / 0 |
| cal.com 22345 | 0 / 2 / 2 | 0 / 2 / 2 |
| Discourse 1 | 2 / 1 / 2 | 2 / 1 / 2 |
| Discourse 2 | 2 / 3 / 0 | 1 / 7 / 1 |
| Discourse 3 | 2 / 2 / 1 | 2 / 2 / 1 |
| Discourse 5 | 1 / 0 / 2 | 1 / 5 / 2 |
| **Total** | **9 / 9 / 7** | **8 / 23 / 8** |

These are the existing automated judge's labels on six completed matched PRs,
not independently adjudicated ground truth or a complete corpus result. The
corresponding candidate counts are 19 earlier and 33 newer; matching and duplicate
classification mean candidate counts need not equal TP + FP. All six newer
responses were partial; four earlier responses were complete and two partial.
No post-correction precision, recall, cost, or latency improvement is claimed.

## Tool availability and failure evidence

The deployed verifier registered four in-process FastMCP tools:
`queryCodeGraph`, `readReviewFile`, `grepReviewCode`, and `getReviewDiff`.
Tools executed: RAG logs contained 344 `POST /query/review-graph` requests and ten
review-generation requests. Response diagnostics referenced retrieved `read-N`
evidence. The problem was not complete absence of tool execution.

The verifier requested tools through model-authored JSON rather than native tool
calls. Logs contain attempted nonexistent names `greadReviewFile`,
`ggrepReviewCode`, and `greedReviewCode`. The inspected interval also contains
194 `grepReviewCode status=partial` warnings, five ambiguous graph responses,
and two unavailable file-read warnings. These counts do not establish the total
successful tool-call count: successful local reads were not individually logged.

A read-only reproduction used the deployed `LocalReviewSource` against retained
Discourse target snapshot
`/tmp/codecrow-rag-branch-generation-discourse-e25638dab0d4b98f99c8fe8976ccaae8f4fb9db3-de688e7e3855b3b6`:

```python
source.grep("email", paths=["app/models"], side="target")
# status=partial, complete=False, unavailablePaths=["app/models"], no matches
source.grep("email", paths=["app/models/user.rb"], side="target")
# status=ready, complete=True, unavailablePaths=[], one matching file
```

Directory selectors were treated as file reads. This matches the Discourse 3
response diagnostics reporting unavailable paths `app/middleware`, `lib`, and
`config`. It does not prove every partial search had this cause.

The corrected `LocalReviewSource` was then checked against the **same retained
snapshot**, loading current source into an isolated `python -B` process's memory
inside the existing container. No container files, services, repository data,
or bytecode were changed, and no model/review/benchmark call was made:

| Selector, literal `email`, target side | Before | Corrected |
| --- | --- | --- |
| `app/models` | partial, unavailable directory, zero matches | ready/complete, no unavailable paths, **16 matching files / 171 matching lines** |
| `app/models/user.rb` | ready/complete, one matching file | ready/complete, **one matching file / 58 matching lines** |

This establishes the real-repository directory-selection fix. It does not
measure model cost, finding quality, or corpus-wide retrieval coverage.


## Unsupported published claims

The response artifacts identify the verifier's unresolved-retention policy as a
concrete false-positive escape path:

- **cal.com 8087:** diagnostics state that every visible changed app-store
  consumer was migrated to `await`, with no remaining synchronous consumer
  identified. The incompatible-consumer claim was retained because the verifier
  could not establish a complete consumer inventory.
- **cal.com 22345:** diagnostics report no production caller of removed
  `InsightsBookingService.findMany`; possible external callers were sufficient
  to preserve the candidate as uncertain.
- **Discourse 2:** diagnostics describe the static-vs-dynamic route conflict as
  dependent on unknown route-recognizer precedence, including the possibility
  that normal static-route precedence makes the change work. The candidate
  remained unresolved and retained.

These are not established defects merely because source acquisition is
incomplete. Retaining unsupported assertions as ordinary published issues
confuses analysis degradation with evidence of a regression. Missing optional
context should remain observable without manufacturing positive findings.

## Observability limits and correction verification

Current-stage model calls logged aggregate token usage but did not populate the
existing review-quality prompt captures. The latest captures predate this run.
Consequently a full replay of exact prompts, successful local-tool arguments,
and evidence selection cannot be reconstructed from the inspected artifacts.

Implementation and contract tests can validate tool schemas, bounded source
selection, candidate disposition, context reuse, and scheduling behavior. They
cannot prove improved real-world precision, recall, monetary cost, or latency.
The user's later paired benchmark remains necessary for those claims.


Documentation verification for the corrective work:

- Updated the existing review orchestration, context assembly, MCP overview,
  inference, configuration, testing, architecture/data-flow, quality and
  troubleshooting pages, plus the inference service README.
- Built an isolated copy at
  `/tmp/codecrow-verifier-docs-build-ye4305hc` with
  `VITE_APP_URL=https://codecrow.app VITE_CLOUD_URL=https://codecrow.cloud npm run build`.
  TypeScript, client build, prerender and SEO verification passed: 150 canonical
  pages and 6,261 root-relative anchors. Build log:
  `/tmp/codecrow-verifier-docs-build.log`.
- Checked MDX loading and App routes for all 12 changed developer pages, plus
  their 17 internal-link occurrences (eight distinct destinations): passed.
- Existing large-chunk and stale Browserslist-data warnings did not fail the
  build. No deployed assets or running services were rebuilt.


## Same-commit Java partial retries

Read-only contract inspection found a separate, existing persistence limitation:
`CodeAnalysisService.resetAnalysisForRetry` merges old issues when both the
stored result and retry are partial. A complete retry clears/replaces them.
`PullRequestAnalysisProcessor` includes only ACCEPTED analyses in request
history, so earlier partial issues are not sent to the verifier with stable
identities. The current inference response has no historical issue-retraction
contract; omitting an issue from an incomplete attempt cannot safely establish
that a prior issue was disproved. Thus an earlier unsupported issue can survive
in the durable same-commit result until a complete retry. This corrective task
does not guess historical mappings or migrate prior findings.

The inspected benchmark harness calls inference `/review` directly
(`benchmark/codecrow_crb_harness.py`, calls near lines 5614 and 5779) and bypasses
Java persistence. This partial-merge behavior does not explain its observed
regression and does not prevent the corrected fresh inference result from
withholding unsupported hypotheses. Existing Java contract tests explicitly
cover partial-to-partial retention and complete-retry replacement.


## Corrective implementation checks

The final full inference suite passed **790 tests** with:

```bash
/tmp/codecrow-ci-python-1000/inference/bin/python -m pytest -p no:cacheprovider tests -q
```

The suite includes a subprocess wrapper that executes **14 real provider-adapter
protocol checks** without paid requests. Contract tests cover native message/tool
pairing, graph-route binding, complete source provenance, directory grep, retry
after unavailable evidence, semantic progress, context retirement,
discovered-finding deduplication, revisable decisions, malformed-output salvage,
and unsupported-claim handling. `git diff --check` passed. Two dependency warnings
remained (Starlette/AnyIO deprecation and FastMCP/Pydantic lifespan definition),
with no failures. These are offline implementation checks, not a paid model or
quality benchmark.

The final full RAG suite passed **386 tests**. The focused traversal group passed
66 tests, including 24 unchanged explicit-budget golden-response oracles.
Default/explicit `None` preserves selected navigation metadata, frontier and
identities beyond 2,000 tokens; optional explicit-budget behavior remains covered.
API forwarding and verifier calls preserve `None` and omit inline source during
navigation. Together the final inference and RAG runs passed **1,176 tests**;
the focused runs are overlapping verification, not extra independent test counts.

The final isolated Docs build after all tool/telemetry changes passed again:
150 canonical pages, 6,261 root-relative anchors, and all 12 changed-page route/MDX
bindings plus 17 internal links. The build path/log above contain the final output.

The corrected implementation logs native/JSON tool inventory, individual tool
execution/cache outcomes, source/result sizes and duration without source text.
Verifier work logs identify PR, pending counts and evidence IDs. This improves
execution traceability for a subsequent benchmark; it does not establish lower
paid cost or better FP/FN outcomes.

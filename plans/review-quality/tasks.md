# Review quality implementation checklist

Plan: [roadmap.md](roadmap.md). Evidence and source map: [investigation.md](investigation.md). Unchecked items are future work; none of the runtime redesign is implemented by this planning task.

## Completed investigation

- [x] Read the supplied objective and applicable engineering/documentation instructions.
- [x] Trace current Java acquisition, serialization, Python review, RAG reads, Java ingestion/tracking, recovery, and QA paths.
- [x] Compare `8dd96da2696340fc05c91baa73ef4121481bc805` against `d0e8d6cabd3194b4bfbeab612477954b302847fa`, including removed planner, batching, verifiers, deduplication, reconciliation, prompts, tests, and Java contracts.
- [x] Check primary research R1–R8 and distinguish source observations from quality hypotheses.
- [x] Execute six provider-free service characterizations: oversized connected group, unchanged intermediary, multi-method hunk, same-turn pending read, lost deletion side, disabled graph.
- [x] Run existing inference diff/DTO/RAG-client/queue tests: **52 passed**.
- [x] Run existing RAG graph-tools, graph-API, and proposed-tree delta-oracle suites: **passed**.
- [x] Run focused Java request/diff/tracking tests: **28 passed**, no failures/errors/skips in their Surefire reports.
- [x] Run public Docs build. Plain `npm run build` failed SEO checks because local environment values emitted localhost links. The README-documented public URL overrides made the full build pass; no environment file was edited.
- [x] Create the three handoff documents; check their local links and keep public implementation documentation unchanged because runtime behavior has not changed.
- [x] Leave the running benchmark, deployed services, model/provider settings, and existing user edits untouched.

These are baseline/architecture checks, not a paired quality experiment. Existing tests can pass while the gaps in the investigation remain.

## Execution rule

For every behavior below: add the failing regression first, implement the smallest cohesive change, then run the affected Python and Java boundary tests. Shared fixtures live under `analysis-plugins/`; concrete language/framework logic stays in its plugin. Update the owning public Docs pages listed in the roadmap during the same implementation phase. Do not deploy to the active benchmark instance.

## P0 — Regression fixtures and baseline

- [ ] Create an isolated implementation checkout and record its source revision plus relevant dirty changes; do not modify the benchmark checkout's runtime files.
- [ ] Add permanent Python service regressions for I1–I7 and Java serialization/status/tracking regressions for I8–I12. Label baseline characterizations separately from desired-behavior tests.
- [ ] Extend shared fixtures with Java and Python defects and counterexamples; include cross-file/unchanged-source proof, deletions/renames, a multi-method hunk, and a 101-file change with late-page evidence.
- [ ] Add Java→JSON→Python and Python→JSON→Java fixture tests for identity, full/delta evidence, optional context, candidates, lifecycle IDs, and partial results. Evolve existing contracts without version fields or duplicate DTO trees.
- [ ] Preserve the completed baseline benchmark's pinned inputs/raw outputs/judge records; adapt harness counting from obsolete `reviewedScopeIds` to the implemented hunk field. Keep experimental outputs separate and immutable.

**Exit:** permanent failing tests identify the intended fixes; no runtime component or numeric quality claim is added merely to instrument evaluation.

## P1 — Evidence and bounded discovery

- [ ] Extract evidence access and prompt/task allocation from `ReviewService`; keep one orchestration entry point and neutral contracts.
- [ ] Adapt current RAG reads and the existing local repository MCP reader behind the same request-scoped interface. Add only missing read/search capabilities, with ranges, paging, completeness, and deletion shadowing.
- [ ] Test host-bound tenant/repository/revision parameters, path traversal, symlink escape, concurrent request caches, stale receipts, deleted overlay files, and local-only operation without provider credentials.
- [ ] Replace graph/snapshot enrichment blockers with useful source/diff fallbacks and existing-channel diagnostics. Incompatible evidence is ignored, never relabeled as exact.
- [ ] Preserve complete full-PR and delta worklists; split by owned source/hunk ranges. Test multiple units per hunk, unsupported parsers, unrepresented ranges, a large component, and a single oversized hunk.
- [ ] Restore project token-budget transport; bound rendered prompts, retrieved windows, output reserve, calls, and task time. Retain exact evidence references across compaction; do not drop later hunks or require all graph pages before reviewing.
- [ ] Fix read-before-dependent-finalization and finding-versus-coverage sequencing. Retain supported results from successful tasks when another task fails or exhausts its budget.
- [ ] Persist `PARTIAL` correctly; test cache exclusion, incremental baseline selection, reporting, retry, and branch/PR completion bookkeeping.
- [ ] Carry old/new side, path, and actual diff-base identity through Java storage and provider publication; use a correctly labeled summary when inline placement is unsupported.
- [ ] Preserve durable job recovery, acknowledgement, lease behavior, cancellation, and snapshot cleanup. Extend existing recovery tests rather than introducing another queue framework.

**Exit:** core review remains useful without auxiliary services; bounded contexts and completion semantics are covered by behavior tests.

## P2 — Verification and semantic duplicates

- [ ] Introduce one verifier role using scoped repository tools and request-local candidate IDs. First prove true/false/uncertain cases with real fixture source and scripted tool interactions.
- [ ] Test refutation by an unchanged caller, already-applied fix, missing graph edge, paginated search, missing source, and a true defect that a skeptical prompt might incorrectly dismiss.
- [ ] Add AI duplicate decisions based on failure cause/trigger/fix. Test cross-file paraphrases, distinct failures at the same location, canonical locations, and candidates split across verifier batches.
- [ ] Retain originals on missing IDs, incomplete output, contradictory mappings, timeout, or absent counterevidence. Add unverified diagnostics without making optional verification a core failure condition.
- [ ] Align Java ingestion with canonical IDs/related locations so location/narrative heuristics do not erase distinct verified findings. Preserve user dismissal and lifecycle identity separately from semantic duplicates.
- [ ] Compare supplied-evidence verification with tool-enabled verification; record incorrectly refuted TPs and incorrect merges as losses. Keep model/provider unchanged for the first ablation.

**Exit:** one optional verifier replaces neither recall with confidence thresholds nor source evidence with model agreement; retain it only on measured benefit.

## P3 — Cross-file tasks and optional planner

- [ ] Expose the full changed-file/hunk manifest and sibling diffs to every worker; keep actual source retrievable instead of expanding every prompt.
- [ ] Allocate interaction tasks for split components, unchanged intermediaries, and neutral framework/data-contract relationships. Use the same discovery worker and avoid duplicate follow-up questions.
- [ ] Test producer/consumer/config/test changes that have no direct changed-to-changed call edge, including evidence outside the current delta.
- [ ] Add the bounded planner experiment: task questions and links from manifest/source summaries, rules, and task intent; no file-skipping authority or fabricated implementation facts.
- [ ] Test missing title/description, planner omission, invalid IDs, missing graph, over-budget input, and late manifest pages; deterministic work survives every optional failure.
- [ ] Compare deterministic tasks, explicit interaction tasks, and planner-assisted tasks separately. Omit the planner from the selected implementation if its benefit is not established.

**Exit:** cross-file interactions have explicit ownership; the planner is a measured option, not a compulsory new stage.

## P4 — Incremental and branch lifecycle

- [ ] Restore consumed previous-issue/previous-commit/reconciliation/analysis-type transport; verify both ecosystem adapters with the same fixtures.
- [ ] Keep full PR evidence available while delta ranges drive new work. Cover force-push, target advancement, missing lineage/delta, abbreviated provider revisions, and prior partial results; fall back to full review when needed.
- [ ] Use the shared verifier for affected historical issues and ambiguous branch reconciliation; carry unaffected open issues forward without re-reviewing everything.
- [ ] Require behavioral evidence for resolution; test disappearing/moved anchors without a fix and genuine cross-file fixes with an unchanged anchor.
- [ ] Preserve dismissal, issue IDs, lineage, authorized project scope, cache identity inputs, and idempotent publication across retries/recovered jobs.

**Exit:** full, incremental, and reconciliation-only purposes work through the same current contracts, with no false resolution from missing context.

## P5 — QA repository investigation

- [ ] Extend QA's Java/Python request with scoped source identity and project rules; acquire or lease evidence for the QA job's own lifetime, including retries after review cleanup.
- [ ] Add the shared read/search/graph interface to QA analysis and cross-impact work. Test retrieval of unchanged callers/tests and source-backed task acceptance criteria.
- [ ] Include authorized same-task PR history without reviving the deleted broad task-history service; test identical task keys in different projects/workspaces.
- [ ] Test concrete regression scopes, preconditions, steps, expected outcomes, edge cases, and evidence references; unknown requirements remain explicitly unconfirmed.
- [ ] Preserve templates, output language, section markers, zero-issue PR handling, same-PR delta updates, multi-PR accumulation, and existing document/publication contracts.
- [ ] Degrade on absent Jira/graph/source and preserve prior cases; do not make QA success a prerequisite for code review or introduce new write-tool permissions.

**Exit:** QA uses repository evidence when needed and produces useful cases under partial context.

## P6 — Comparison and implementation handoff

- [ ] Run paired arms only in an isolated environment or after the active benchmark is released. Keep corpus/model/provider/judge configuration fixed and preserve raw candidates, decisions, and final findings.
- [ ] Report TP/FP/FN, precision, recall, F1/F2, true findings lost to verification, duplicate burden, unresolved scopes, costs/tokens/calls, and latency. Score incomplete runs explicitly; do not remove them from recall denominators.
- [ ] Adjudicate novel/disputed findings consistently across arms, inspect per-PR and cross-file/large-PR changes, and report paired uncertainty. A ten-PR diagnostic is not a general quality claim.
- [ ] Select the smallest configuration with demonstrated benefit; record negative results and unresolved cost/recall tradeoffs rather than adding more agents or filters.
- [ ] Complete the compatibility matrix below and the relevant plugin/host fallback tests.
- [ ] Update existing public Docs owners for actual implemented behavior and failure semantics, check affected `/docs/...` routes in `src/App.tsx`, and run `npm run build` with documented public URL values.
- [ ] Handoff exact changed files, tests run, paired evidence, remaining limitations, and deployment status. Do not mark runtime work complete from this checklist alone.

## Compatibility and failure matrix

Parameterize supported provider paths for **GitHub, GitLab (including configured self-managed base URL), and Bitbucket Cloud**. The presence of a `BITBUCKET_SERVER` enum is not proof of a working review adapter; do not silently expand supported-provider claims.

| Entry point / condition | Required assertion |
| --- | --- |
| PR first run, synchronize/new commit, rename/delete, missing PR description | Complete acquisition or explicit reduced context; correct range/side; all eligible changed work retained. |
| Branch direct push and ambiguous issue reconciliation | Correct purpose dispatched; usable supplied evidence works without a PR overlay. |
| Manual run and comment-command analysis | Same authorized repository/rules/history semantics; caller cannot widen scope. |
| Restart, lost delivery, worker crash, expired lease, duplicate delivery | Persisted jobs are recovered or explicitly failed; no indefinite `init`; no duplicate publication or snapshot leak. |
| Graph/RAG unavailable, stale receipt, missing parser/plugin, partial overlay, Jira outage | Core diff/source review continues when possible; uncertainty is visible; historical issues are not silently resolved. |
| Model timeout, invalid JSON, tool failure, oversized connected change, output exhaustion | Useful partial findings retained; no clean-success cache entry for unreviewed work. |
| Cross-tenant/project access, hostile paths/symlinks, prompt instructions inside source/task text | Server-owned binding holds at each read/data-access boundary. Access denial does not unlock a broader fallback. |

Test these with mocked providers and existing integration fixtures first. Live paid evaluation is a later quality comparison, not a substitute for Java/Python correctness tests.

## Verification commands

Commands used successfully for this investigation (from the stated directories, with existing environments). New test filenames in future phases must be added to the appropriate suite; no removed stage/gate command is assumed available.

From `codecrow-public/python-ecosystem/inference-orchestrator`:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  ../../.coverage-venvs/inference/bin/python -m pytest -q -p asyncio -p no:cacheprovider \
  tests/test_diff_processor.py tests/test_dtos.py \
  tests/test_rag_client.py tests/test_queue_consumer.py
```

From `codecrow-public/python-ecosystem/rag-pipeline`:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  ../../.coverage-venvs/rag/bin/python -m pytest -q -p asyncio -p no:cacheprovider \
  tests/test_review_graph_tools.py tests/test_review_graph_api.py \
  tests/test_proposed_tree_delta_oracle.py
```

From `codecrow-public/java-ecosystem`:

```bash
mvn -o -q -pl libs/analysis-engine -am \
  -Dtest=PullRequestDiffPreparationServiceTest,PrIssueTrackingServiceTest,AiAnalysisClientTest \
  -Dsurefire.failIfNoSpecifiedTests=false test
```

Future Java changes also require the affected `libs/core`, `services/pipeline-agent`, `libs/vcs-client`, and `mcp-servers/vcs-mcp` tests; snapshot, provider reporting, QA generation, and recovery tests already exist. Future Python changes require the new review/verifier suites plus existing QA, agent, and neutral-plugin tests. The commands above are a baseline subset, not the complete implementation acceptance suite.

From `codecrow-static`, following its README:

```bash
VITE_APP_URL=https://codecrow.app VITE_CLOUD_URL=https://codecrow.cloud npm run build
```

This builds locally; it does not deploy. Generated output, Maven `target/`, Python caches, and temporary fixtures must remain uncommitted.

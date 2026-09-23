# Review quality investigation

Investigated on 2026-09-23. Implementation handoff: [roadmap.md](roadmap.md), [tasks.md](tasks.md).

## Scope and evidence

This is a planning deliverable, not an implemented redesign. The running `codecrow-public` benchmark was not redeployed, restarted, or used for new model calls. Existing staged and unstaged changes were left intact. No previous proposal, saved conversation, or abandoned patch was used as design authority.

The local source HEAD is the [requested commit, `8dd96da2696340fc05c91baa73ef4121481bc805`](https://github.com/rostilos/CodeCrow/commit/8dd96da2696340fc05c91baa73ef4121481bc805); its parent is `d0e8d6cabd3194b4bfbeab612477954b302847fa`. GitHub's web view could not be fetched, but both complete Git objects are available locally. Investigation used the actual diff and parent source, not the commit description. The commit changes 175 files: 1,399 insertions and 67,351 deletions. Historical source was extracted into a temporary directory without checking out the old implementation.

Evidence labels used below:

- **Observed:** source behavior or a provider-free execution against the current code.
- **Hypothesis:** a plausible cause of poor review quality, requiring paired model evaluation.
- **Proposal:** a behavior to implement and evaluate; it is not a current guarantee.

The reported increase in recall and decrease in precision/cost is the user's observation. This investigation does not establish its magnitude or attribute it causally to one removal. No fixed, adjudicated before/after result was generated during this task.

## Current execution path

1. Java's `AbstractVcsAiClientService` obtains PR metadata, prefers a base-to-head commit-range diff, and falls back to the provider PR diff. `PullRequestDiffPreparationService` retains the full scoped diff, chooses a useful previous-head delta when available, and separately retains the complete PR path list for the proposed-tree overlay.
2. `PullRequestAnalysisProcessor` fetches proposed file contents and stages a target-head snapshot plus changed-file/deletion overlay. Java enrichment currently fetches file contents rather than the former capability-aware AST/context contribution. `AiAnalysisClient` sends one review request over Redis.
3. Python's `ReviewService` selects `deltaDiff` in incremental mode, otherwise `rawDiff`. It parses hunks, requires snapshot/overlay identity, and prepares a sealed proposed-tree graph. Preparation and initial graph-query failures abort review before useful core analysis.
4. Each hunk is one `ReviewPart`. The host selects the smallest proposed-tree unit overlapping **any** anchor in that hunk. It joins parts in the same unit and parts connected by call, implementation, inheritance, or test edges between changed units.
5. Each connected component gets a separate conversation, processed sequentially within one review. The model sees its undecided hunks, units, graph relations, accumulated read results, previous findings in that conversation, PR text, rules, and task context. The semaphore bounds concurrent reviews, not concurrent groups within a review.
6. The model requests graph/unit/file/diff/literal-search reads in JSON. The host executes them through `RagClient`; this is already a tool-using review loop even though review no longer uses MCP transport. Repository reads can reach unchanged files. Diff reads only see the selected worklist's paths.
7. `_finding` checks hunk ID, path, line, title, and reason. There is no independent semantic verification. Final inference deduplication uses `(file, line, lowercased title)`. Java ingestion performs additional within-file identity/narrative deduplication, which the direct inference benchmark does not exercise.

Primary sources: [review service][review], [diff preparation][diff-java], [Java producer][producer], [request serialization][client], [PR processor][pr], [RAG review context][rag-context], [ingestion][ingestion], [benchmark harness][harness].

## Findings that change the roadmap

| ID | Observed behavior | Consequence / interpretation |
| --- | --- | --- |
| I1 | `_group_parts` has no rendered-prompt budget or subdivision of a large component. `_graph_results` eagerly collects all relation pages. | A 100+ file logical component can still create one very large initial prompt. Smaller components can instead require many sequential calls. A token-aware work allocator is missing, not all smart grouping. |
| I2 | Two changed units joined through an unchanged unit are not joined; framework/data-contract relation kinds do not generally participate in the join filter. Groups share a read cache, not decisions or review questions. | Retrieval can expose the missing context, but no task owns the interaction. Structural proximity alone cannot cover logical changes spanning config, producer, consumer, schema, and tests. |
| I3 | One hunk can span several functions, but receives one chosen unit and one completion bit. Deletion coordinates are compared with proposed-tree units. | “All hunks reviewed” is weaker than all changed behavior considered; old/new coordinate spaces can be confused. Preserve hunk provenance while assigning smaller source ranges when necessary. |
| I4 | `_review_group` accepts a finding and marks its part reviewed before processing reads; when all expected parts are reviewed, it breaks immediately. | A response can request the evidence needed to check its own claim and publish the claim without reading it. This is a concrete sequencing defect, separate from the verifier hypothesis. |
| I5 | Every later prompt includes accumulated evidence and accepted findings. Progress has a no-new-evidence stop, but there is no total turn/input/output/read budget. | Unique requests can keep the loop growing. Paging alone does not bound prompt growth; complete prior pages are retained. |
| I6 | No verifier; tuple dedup can retain paraphrases across files and collapse different causes sharing a location/title. | Missing semantic checks plausibly increase FPs and duplicate comments. Removing old rejection rules could also recover genuine findings. Neither effect is measured here. |
| I7 | Missing graph, exact structural binding, overlay, or preparation receipt causes `error`, despite Java treating snapshot staging as optional. | This violates the supplied fail-open requirement for auxiliary context. Discard incompatible context, then review available diff/source with an explicit diagnostic. Never substitute another tenant's or revision's evidence. |
| I8 | The producer builds previous-issue data, but serialization and Python DTO omit previous issues, previous commit, reconciliation contents, and analysis type. Python's incremental diff lookup also omits earlier PR changes. | Delta selection survives; semantic lifecycle reconciliation does not. An unchanged part of the PR can matter to a new delta even when it is outside the current worklist. |
| I9 | Java PR tracking still matches identities and preserves unresolved issues when content/anchors are unavailable or still present. It can resolve an omitted issue when its old anchor disappears. | Reconciliation was not completely removed. However, anchor disappearance is not proof that the behavioral defect was fixed; rename/reformat cases need semantic rechecking. |
| I10 | Branch reconciliation still calls `performAnalysis` with previous issues and file contents, without a prepared snapshot. Those fields no longer cross the wire, and the former branch reconciliation dispatch is gone. | The remaining call path cannot perform its intended AI reconciliation with the current review contract. Its Java exception handler retains issues, so this is degraded reconciliation rather than evidence that all branch jobs fail. |
| I11 | `AnalysisStatus.PARTIAL` exists, but `CodeAnalysisService.fillAnalysisData` always assigns `ACCEPTED`. PR processing uses the raw partial response to skip tracking/DAG completion on that run. | Persistent status can later admit incomplete results to accepted-history/cache paths. Fix transport-to-storage semantics before using those records as incremental baselines. |
| I12 | Removed-only parts carry `side="target"`, but `_finding` drops side and old path. Mixed hunks use added anchors when present. | Inference acceptance does not guarantee a correct provider inline anchor or later tracking. Old-side revisions must reflect the actual diff base, which need not equal target HEAD. |
| I13 | Rules and task context still reach review prompts. Cross-PR task history and plugin review contributions were removed. Structural language/framework plugins still exist in indexing. | Do not describe this as removal of all rules, Jira integration, or plugins. Restore only the missing, useful context through neutral boundaries. |
| I14 | QA retains single-pass and batch/cross-impact/aggregation paths, prior documentation, delta input, templates, task context, and file enrichment. It directly invokes the LLM and has no repository evidence tool session. Its request has no equivalent pinned review evidence binding or project-rules field. | Passing a provider name or calling the workflow “multi-stage” does not give it tools. QA needs explicit, scoped read/search access to investigate affected unchanged callers/tests and distinguish task intent from implementation. |

These are runtime/data-flow findings, not a proposal for new enforcement layers. Existing security checks remain mandatory; missing optional context must not become a new reason to reject analysis.

### Provider-free characterizations

Temporary fake graph and LLM implementations exercised the real `ReviewService` methods; no provider, Docker service, or paid model was used:

| Input | Current result |
| --- | --- |
| 101 changed units connected as a call chain; 4,000 diff characters each | One group containing all 101 parts and 404,000 diff characters, before prompt overhead. |
| 101 changed units with only an unchanged intermediary connecting them | 101 groups. |
| Hunk with anchors in two separate methods | Only the first of two equal-sized candidate units selected. |
| Valid finding plus a requested corroborating file read in the same response | Finding accepted; part marked reviewed; zero reads executed. |
| Removed-only finding | Returned issue has no side field. |
| Usable diff with disabled graph client | `status="error"`. |

These demonstrate implementation behavior only. They establish no FP/FN, cost, latency, or model-quality improvement. The first implementation phase should make the necessary cases permanent regression tests.

## What the requested commit actually removed

Historical paths in this table are relative to `python-ecosystem/inference-orchestrator/src/` unless marked Java. Read any original with `git show d0e8d6ca:<path>` from `codecrow-public`.

| Area inspected | Parent behavior | What is worth retaining / avoiding |
| --- | --- | --- |
| `service/review/orchestrator/stage_0_planning.py`, `utils/prompts/constants_stage_0.py` | Planner grouped/prioritized files and listed cross-file concerns using file metadata, up to 12 representative hunk headers and 16 changed lines, each limited to 240 characters. Small/oversized/unusable planning requests fell back to a local plan; mechanical exclusions included deleted files. | Retain optional planning and conservative fallback. A sampled summary is not proof of full PR understanding; do not let an LLM remove work or restore blanket exclusion of deletion regressions. |
| `stage_1_file_review.py`, `stage_1_local_packing.py`, retained `utils/dependency_graph.py` | Enrichment/exact graph components, rendered-input budgeting, split diff/source evidence, outside-batch relationship hints, bounded parallel execution. An individual batch failure could cancel siblings; extensive coverage/invocation bookkeeping surrounded execution. | Retain dependency-aware ownership, boundary hints, and prompt accounting. Reimplement small components; do not restore 4,099-line review and 1,650-line packing modules. Preserve successful siblings. |
| `verification_agent.py` | Stage 1.5 combined regex/anchor/provenance rejection with an optional LLM loop. Its tool searched already-enriched full files, not the entire repository. Failed/omitted optional verification batches retained issues. | The old verifier was narrower than “graph-enabled verifier.” Keep fail-open decisions and source checks; replace language/prose heuristics with evidence-based verification. |
| `stage_2_cross_file.py`, `stage_2_semantic_packets.py`, `service/review/pr_evidence.py` | Cross-file calls used first-stage findings, full PR/delta evidence roles, architecture, rules, task context/history, and planner concerns. Evidence was packetized; missing optional shards degraded. | Retain explicit cross-file questions and full-PR visibility in incremental runs. Avoid an unconditional second review of every PR or a new synthesis hierarchy. |
| `reconciliation.py` | Historical issue carry-forward, line updates, exact/conservative dedup, and LLM dedup over host-selected similarity components. Ambiguous failures retained candidates. | Retain stable lifecycle identity and semantic merging. Do not let file/line/text-similarity heuristics decide which cross-file causes can ever be duplicates. |
| `stage_3_aggregation.py`, `stage_3_mcp_verification.py`, `mcp_tool_executor.py` | Report shards, optional final tool verification, evidence-bound dismissal checks, then synthesis. The parent already bounded this verification to one tool-selection call plus one finalization call. | Do not attribute a 15-turn final loop to this parent; that is only a historical comment. Replace separate report-time filtering with the single verifier's decisions and deterministic formatting. |
| `orchestrator.py`, `inference_policy.py`, `candidate_ledger.py` | Orchestrated planning, review, reconciliation, Stage 1.5 verification, conditional cross-file review, more evidence gates/dedup, aggregation, and final dismissals. | Removing only “the verifier” is not an isolated experiment: multiple context, filtering, and execution changes happened together. Avoid rebuilding this stack. |
| Java `AiAnalysisClient`, request DTOs, `AbstractVcsAiClientService`, project configuration | Removed review MCP credentials/toggle, token-limit transport, previous issues, task history, capabilities, enrichment serialization, and related identity inputs. Deleted capability-selection and task-history services. | Restore only consumed fields, with Java/Python contract tests. Keep credentials out of prompts. Respect the existing project token setting rather than inventing parallel configuration. |
| Java PR/branch processors and reconciliation | Always attempts local review staging; adds raw partial handling; removes dry-run special paths. Existing ambiguous branch reconciliation producer remains. | Preserve locks, provider fallbacks, and partial findings. Repair the missing consumer behavior rather than reverting processors wholesale. |
| RAG `review_context.py`, `structural_store.py`, API/query models; TypeScript plugin | Adds/changes exact file and literal-search access and proposed-tree behavior; TypeScript resolves `this.method` through enclosing class names. | Keep current graph/source improvements. This commit is not just a deletion of orchestrator stages. |
| Tests and `tools/review_quality` | Deletes planner/reviewer/verifier/reconciliation/packing tests, candidate/capture tooling, prompt dry runs, seeded gates, and deployed paired replay utilities. Retains corpus preparation and normal test infrastructure. | Recreate targeted behavior tests and reuse the current benchmark harness. Do not restore the removed gate/capture platform or assume deleted commands still exist. |
| QA and commands | QA's orchestration survives; shared agent JSON helpers move under `service/agent`. MCP configuration, agent execution, VCS MCP, and RAG MCP modules remain. | The removal is specific to review orchestration; reuse surviving infrastructure without coupling QA to review verdicts. |

The Dockerfile/compose diff also removes dry-run/capture wiring and changes the Python base image. The inference images still include a JRE, the VCS MCP JAR, and the plugin bundle: local MCP reuse needs no new sidecar. Validate packaged tool behavior in an isolated build later; do not change the active benchmark deployment.

The commit-wide file inventory and these execution paths were inspected across both revisions. This is not a claim to have executed or audited every deleted test or all 67,351 deleted lines.

## Technical research and decision limits

Research supports mechanisms and experiments, not a promised CodeCrow score. Sources were accessed on 2026-09-23. The exact papers and limitations matter more than leaderboard weights.

| Ref | Primary resource | Supported mechanism and limitation | Roadmap use |
| --- | --- | --- | --- |
| R1 | Liu et al., [Lost in the Middle](https://arxiv.org/abs/2307.03172), TACL | Relevant evidence position affects retrieval/QA performance; a large context window does not imply reliable use of all context. These experiments are not code-review evaluations. | Bound rendered prompts, preserve retrievable references, and test evidence beyond the first page. No universal “safe” token threshold is implied. |
| R2 | Yang et al., [SWE-agent](https://arxiv.org/html/2405.15793), NeurIPS 2024, §§3, 5 | Repository search/view interfaces and context management were experimentally ablated for software repair. It supports deliberate tool design, not the claim that MCP transport itself improves review quality. | Reuse small, paginated read/search/graph tools; compare transport only if integration requires it. |
| R3 | Gou et al., [CRITIC](https://arxiv.org/html/2305.11738), ICLR 2024, §§3–4 and limitations | External tool feedback supports critique/correction in QA, mathematical program synthesis, and toxicity tasks. Added latency and task transfer remain limitations. | One evidence-enabled verifier, with bounded calls; measure false dismissals as well as removed FPs. |
| R4 | Huang et al., [Large Language Models Cannot Self-Correct Reasoning Yet](https://arxiv.org/abs/2310.01798), ICLR 2024 | Intrinsic self-correction without external feedback can fail or worsen reasoning. It is not a blanket impossibility result for all current models or tools. | A fresh “check again” prompt alone is insufficient justification for a verifier. Require access to counterevidence and retain uncertainty. |
| R5 | Qiu and Gill, [Adversarial Review](https://arxiv.org/html/2608.18167), 2026 workshop paper, §4 | In its 100-PR review experiment, naive reviewer/critic interaction underperformed; constrained, grounded disagreement improved its reported result. The small study and judge-based evaluation limit transfer. | Ask the verifier to test the failure mechanism and strongest counterexample. Do not restore a debate team or infer guaranteed benefit from agent count. |
| R6 | Kumar, [SWE-PRBench](https://arxiv.org/html/2603.26130), 2026 preprint, §§6, 8 | Its tested frozen context expansions degraded review performance. Evaluation covers a 100-PR sample, is Python-dominant, and uses LLM judges. This is not a test of CodeCrow's adaptive retrieval. | Include focused retrieval versus expanded-context ablations; do not assume more graph/context tokens help. |
| R7 | Zhang et al., [Code Review Agent Benchmark / c-CRAB](https://arxiv.org/html/2603.23448), 2026 preprint, §§3–4 | Uses executable, review-derived tests to assess whether a review captures a concern; text overlap is an imperfect oracle. It is not a complete inventory of every valid defect. | Seed positive and negative behavior fixtures; adjudicate novel findings instead of treating unmatched wording as FP. |
| R8 | [Git diff documentation](https://git-scm.com/docs/git-diff) | Two-endpoint differences and merge-base comparisons identify different before states; rename and deletion representations need explicit interpretation. | Keep full-PR, previous-head delta, target-head, and diff-base identities distinct in incremental and old-side evidence. |

**Planner conclusion:** restore the capability as an optional, bounded experiment after deterministic allocation and cross-group task ownership work. There is no evidence here that always adding Stage 0 improves CodeCrow F1/F2. Its testable purpose is to find logical interactions absent from direct graph edges, not to rank files out of review.

**Batching conclusion:** retain graph grouping as a hint; split large components into source/hunk tasks with explicit boundary questions and PR-wide navigation. Per-file review is a valid fallback only when it can read sibling changes and when cross-file questions have an owner.

**Verifier conclusion:** prioritize one source-enabled verifier that also handles semantic duplicate decisions. Treat `confirmed`, `refuted`, and `uncertain` separately. There is no rationale for the old chain of regex filters, multiple verifiers, and report-time rejection.

**MCP conclusion:** preserve repository tools and use a narrow MCP adapter where needed for the existing local reader. Do not expose the full VCS/platform catalog or make MCP a prerequisite for review. The current JSON read loop can call the same evidence interface.

## Verification and remaining evidence

Verification performed during planning is recorded in [tasks.md](tasks.md#completed-investigation). The tests establish contracts, not quality improvements. Public Docs currently describe the graph-first baseline and its limitations; proposed behavior is deliberately confined to these planning files. Each implementation phase names the existing public page to update when its behavior changes.

Before implementation, preserve the active benchmark's eventual raw output and judge artifacts as an immutable baseline. Do not interrupt it to obtain a historical comparison. The existing harness saves raw responses and recognizes `complete`/`partial`, but currently reads `reviewedScopeIds` while inference emits `reviewedHunkIds`; adapt the harness's count instead of creating another production contract. A successful HTTP/final event is not proof of complete review. Compare raw candidates, verifier decisions, and published findings separately so an apparent precision gain cannot conceal recall loss.

## Source map

Current source entry points, with symbol names above providing the precise lookup:

- [ReviewService][review], [diff parser][diff-python], [snapshot identity][identity], [review DTO][dto], [retained issue schema][issue-schema].
- [PR producer][producer], [PR diff preparation][diff-java], [AiAnalysisClient][client], [PR processor][pr], [snapshot staging][snapshot].
- [PR issue tracking][tracking], [branch reconciliation][branch-reconcile], [analysis ingestion][ingestion], [ingestion dedup][ingestion-dedup].
- [RAG client][rag-client], [proposed-tree context][rag-context], [graph queries][graph-tools], [VCS MCP tools][mcp-tools], [local repository reader][local-reader], [shared agent execution][agent].
- [QA request][qa-request], [QA orchestration][qa], [QA Java producer][qa-java], [dependency batching][dependencies], [neutral plugin contracts][contracts].
- [Webhook recovery][recovery], [queue consumer][queue], [current benchmark harness][harness], [neutral corpus fixtures][corpus].

[review]: ../../python-ecosystem/inference-orchestrator/src/service/review/review_service.py
[diff-python]: ../../python-ecosystem/inference-orchestrator/src/utils/diff_processor.py
[identity]: ../../python-ecosystem/inference-orchestrator/src/service/review/snapshot_identity.py
[dto]: ../../python-ecosystem/inference-orchestrator/src/model/dtos.py
[issue-schema]: ../../python-ecosystem/inference-orchestrator/src/model/output_schemas.py
[producer]: ../../java-ecosystem/services/pipeline-agent/src/main/java/org/rostilos/codecrow/pipelineagent/generic/service/AbstractVcsAiClientService.java
[diff-java]: ../../java-ecosystem/libs/analysis-engine/src/main/java/org/rostilos/codecrow/analysisengine/service/pr/PullRequestDiffPreparationService.java
[client]: ../../java-ecosystem/libs/analysis-engine/src/main/java/org/rostilos/codecrow/analysisengine/aiclient/AiAnalysisClient.java
[pr]: ../../java-ecosystem/libs/analysis-engine/src/main/java/org/rostilos/codecrow/analysisengine/processor/analysis/PullRequestAnalysisProcessor.java
[snapshot]: ../../java-ecosystem/libs/analysis-engine/src/main/java/org/rostilos/codecrow/analysisengine/service/LocalRepositorySnapshotService.java
[tracking]: ../../java-ecosystem/libs/analysis-engine/src/main/java/org/rostilos/codecrow/analysisengine/service/pr/PrIssueTrackingService.java
[branch-reconcile]: ../../java-ecosystem/libs/analysis-engine/src/main/java/org/rostilos/codecrow/analysisengine/service/branch/BranchIssueReconciliationService.java
[ingestion]: ../../java-ecosystem/libs/core/src/main/java/org/rostilos/codecrow/core/service/CodeAnalysisService.java
[ingestion-dedup]: ../../java-ecosystem/libs/core/src/main/java/org/rostilos/codecrow/core/service/IssueDeduplicationService.java
[rag-client]: ../../python-ecosystem/inference-orchestrator/src/service/rag/rag_client.py
[rag-context]: ../../python-ecosystem/rag-pipeline/src/rag_pipeline/core/review_context.py
[graph-tools]: ../../python-ecosystem/rag-pipeline/src/rag_pipeline/core/review_graph_tools.py
[mcp-tools]: ../../java-ecosystem/mcp-servers/vcs-mcp/src/main/java/org/rostilos/codecrow/mcp/McpTools.java
[local-reader]: ../../java-ecosystem/mcp-servers/vcs-mcp/src/main/java/org/rostilos/codecrow/mcp/generic/LocalRepoClient.java
[agent]: ../../python-ecosystem/inference-orchestrator/src/service/agent/agent_execution_service.py
[qa-request]: ../../python-ecosystem/inference-orchestrator/src/api/routers/qa_documentation.py
[qa]: ../../python-ecosystem/inference-orchestrator/src/service/qa_documentation/qa_doc_orchestrator.py
[qa-java]: ../../java-ecosystem/services/pipeline-agent/src/main/java/org/rostilos/codecrow/pipelineagent/qadoc/QaDocGenerationService.java
[dependencies]: ../../python-ecosystem/inference-orchestrator/src/utils/dependency_graph.py
[contracts]: ../../analysis-plugins/contracts
[recovery]: ../../java-ecosystem/services/pipeline-agent/src/main/java/org/rostilos/codecrow/pipelineagent/generic/service/WebhookJobRecoveryScheduler.java
[queue]: ../../python-ecosystem/inference-orchestrator/src/server/queue_consumer.py
[harness]: ../../../benchmark/codecrow_crb_harness.py
[corpus]: ../../analysis-plugins/fixtures/review-quality/neutral-corpus.json

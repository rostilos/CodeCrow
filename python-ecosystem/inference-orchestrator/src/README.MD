# Inference Orchestrator

The inference service consumes review jobs from Java and returns findings with an explicit completion state. Interactive ask, summarize, and QA documentation commands have separate handlers.

## Review path

Java supplies the acquired diff, provider revisions, project identity, staged target tree, and proposed-file overlay. An optional proposed-tree graph supplies structural relationships. The deterministic planner co-owns directly coupled changed contracts, retains complete hunks, and supplies exact companion changes at remaining boundaries; graph failure is reported while diff review continues.

Tool-free batch discovery emits candidate findings and internal behavior, contract, and question summaries in the same response. Each finding explains its causal mechanism once in `reason`, with `sourceLocations` pointing to supporting ranges. Selective synthesis routes remaining cross-batch scopes into source investigations; discovery questions also reach verification directly when the graph is unavailable.

Verification groups related candidates and questions by their affected definitions, falling back to file/side ownership without graph facts. Each case starts with complete relevant hunks and available enclosing source already acquired for discovery. Exact supplied source can support a decision immediately. Native graph, local read, literal-search and diff tools retrieve missing evidence. Original assistant/tool exchanges are appended to the stable case transcript; the whole transcript ends before the next unrelated case begins. Successful observations remain cached within the request.

`recordReviewDecisions` records decisions, investigation answers and report corrections. A candidate containing an incorrect secondary claim can retain its demonstrated defect through corrected wording. Source-supported failures under ordinary language/framework semantics do not require a production incident or deployment-specific reproduction. Existing candidate IDs identify revisions at the original anchor; an explicit different active anchor is evaluated as a distinct finding while the original candidate remains pending.

After all cases, multiple distinct publication records trigger one source-free, tool-free semantic partition. It groups complete causal descriptions and locations, including verifier-created restatements, and selects existing representatives. It cannot rewrite, invent or revalidate issues. Invalid, missing or overlapping groups preserve affected records with diagnostics; provider failure preserves the records without a retry loop. Independently actionable defects may share an anchor.

Context is selected by ownership, relationships, and the question being checked. Complete selected hunks and source units are retained. Summaries and cached evidence avoid repeating full batch inputs; no quality or cost improvement is implied without paired measurement.

A finding must use a changed-code anchor and explain a source-supported failure. Missing or mistyped hunk IDs resolve through an unambiguous exact active file/line; they never permit line guessing or expansion beyond an incremental delta. Inference returns `complete`, `partial`, or `error`. Unresolved discovery, verification or publication-grouping scopes remain explicit. Normal unsupported hypotheses remain unpublished with partial diagnostics. Verifier infrastructure/model failure preserves undecided discovery candidates as an explicit partial fallback; graph failure alone does not force partial status when the core review can finish. Java persists partial outcomes as `PARTIAL`, excludes them from completed-result caching, and refreshes the same durable record on retry. Java owns provider publication.

## Runtime and checks

`RAG_API_URL` configures graph-service access and `REDIS_URL` configures the internal queue. Java supplies model credentials and routing settings per request. Graph receipts and local source roots are scoped to the project and pinned review revision.

Run the focused Python review tests with the service dependencies installed. Benchmarks are a separate operation and require the same fixed corpus, model/provider settings, judge, and token/cost accounting for both implementations. Architecture and unit tests establish contracts, not precision, recall, F1, speed, or cost changes.


## Exact provider capture

For a future run, opt in with `REVIEW_QUALITY_CAPTURE_ENABLED=true` and optionally restrict `REVIEW_QUALITY_CAPTURE_PROJECT_IDS` to comma-separated project IDs. An empty filter captures all review projects. Compose persists artifacts under `/app/logs/review-quality-captures`; standalone inference can choose `REVIEW_QUALITY_CAPTURE_OUTPUT_DIR`.

The capture records actual SDK HTTP request/response bodies, including native tools, reasoning/routing options, complete source and usage. Each SDK attempt has private request/response files and start/completion manifests correlating the project, review run, durable queue job when present, stage, batch, turn, call and provider generation. Request auth headers, URL queries and the credential DTO are not serialized. Directories use `0700`, files use `0600`, and tenants/runs have separate paths. Source and returned text remain intact. Capture errors log a diagnostic without failing review; there is no automatic truncation or retention limit. Streams are copied as consumed, and manifests describe response compression/interruption.

Public operator instructions and architecture are maintained in the sibling documentation repository at `codecrow-static/src/pages/Docs/en/developer/configuration.mdx` and `review-orchestration.mdx`. `tests/provider_capture_protocol_checks.py` compares capture bytes against mocked real-SDK transports without network/model calls. These protocol tests do not establish review quality or paid cost.

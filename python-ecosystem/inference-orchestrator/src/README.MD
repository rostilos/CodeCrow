# Inference Orchestrator

The Python host runs the restored multistage review, PR comment commands and QA
content generation. Language and framework implementations live in
`analysis-plugins/`; the host consumes neutral plugin contracts. Review prompts,
model settings, stage budgets, prior-issue reconciliation and native tool calls
remain owned by the restored review implementation.

The public developer documentation is maintained in the sibling
`codecrow-static/src/pages/Docs` repository:

- [Review orchestration](https://codecrow.app/docs/developer/review-orchestration)
- [Context assembly](https://codecrow.app/docs/developer/context-assembly)
- [Repository indexing](https://codecrow.app/docs/developer/rag-indexing)
- [Configuration](https://codecrow.app/docs/developer/configuration)
- [Testing](https://codecrow.app/docs/developer/testing)

## Runtime ownership

| Package | Responsibility |
| --- | --- |
| `api` | Authenticated HTTP entrypoints, shared NDJSON streams and service lifetime |
| `server` | Redis admission, consumer health, ordered progress and terminal delivery |
| `service/review` | Request identity, evidence coverage, quality capture and review orchestration |
| `service/review/orchestrator` | Planning, file review, source verification, cross-file analysis, aggregation and reconciliation |
| `service/agent` | Request-scoped MCP sessions and native model/tool execution |
| `service/rag` | Exact graph bindings, separate query/preparation HTTP pools and fail-open transport |
| `utils` | Neutral diff evidence, response text, error presentation and MCP configuration |

Stage 0 plans review work. Stage 1 reviews bounded file units and can use
request-bound repository/graph tools when `useMcpTools` is enabled. Eligible
host-loaded relation briefings provide initial orientation; when no briefing is
available, the existing primary agent path can require `getMinimalReviewContext`.
Focused graph and structural-unit tools resolve remaining questions. Exact file
reads use the VCS MCP source tools. Verification, optional Stage 2 cross-file
analysis, Stage 3 aggregation and final deduplication retain their existing
budgets and evidence rules. Historical issue reconciliation remains available.

Graph preparation binds the tenant, repository, logical target/source revisions,
target snapshot and complete declared review overlay. Java sends optional exact
and active branch seed candidates plus the independent project indexing policy.
A compatible seed accelerates preparation; an absent or stale seed permits a full
proposed-tree build with the same policy. Subsequent queries carry the accepted
seed coordinates, including their explicit absence, and the sealed review receipt.
A failed optional graph operation reports a diagnostic and leaves the existing
source-review path available. Missing or incomplete core review evidence follows
the restored review's own failure behavior.

Project `ragEnabled` controls persistent indexing/retrieval. Request-scoped graph
tools can still be prepared from temporary source when `useMcpTools` is enabled;
service-wide `RAG_ENABLED=false` disables the structural client. Neither setting
introduces a different review implementation.

## Capacity and cleanup

`MAX_CONCURRENT_REVIEWS` defaults to `16`. Redis reserves admission before
removing a job from the queue. Preparation, repository reads and actual model
calls have separate capacity. `MAX_CONCURRENT_REVIEW_CALLS` inherits the review
capacity when unset; the model scheduler rotates pending requests between
reviews and releases capacity after each invocation. Existing smaller explicit
settings remain effective. Per-stage parallelism and model-call budgets are
unchanged.

MCP source admission is supplied by the review host through a neutral middleware
callback. Its capacity wait precedes the existing per-connection operation
deadline. RAG preparation and read traffic use separate HTTP connection pools.
The preparation timeout defaults to `1800` seconds; optional query timeout
remains `120` seconds. These controls do not alter provider routing or prompts.

Review and command consumers share lifecycle handling and one ordered event
drain. Their existing Redis keys, result shapes and event TTLs are retained.
NDJSON streams emit idle heartbeats and cancel/join request-owned work on client
disconnect. Shutdown stops both intake loops, drains admitted jobs, and then
closes shared HTTP clients. Partial startup also cleans resources already created.

MCP sessions remain request-scoped. There is no shared credential-bearing JVM
process pool. Obsolete parser/classifier/signature compatibility modules are not
part of the runtime; current review parsing lives in the orchestrator helpers.

## Configuration and verification

Use `deployment/config/inference-orchestrator/.env.sample` for supported options.
Keep provider credentials in the existing request/configuration boundaries.
Prompt dry runs and project-scoped quality captures retain their restored
semantics; actual quality capture still invokes the configured model.

From the repository root, the production entrypoint runs the isolated Python
matrices, Java verification, plugin assembly and all Docker image builds:

```bash
deployment/build/production-build.sh --build-only
```

Omit `--build-only` to start the local Compose services after successful builds.
The option still runs the complete verification/build pipeline. It does not
start a benchmark or make review-model calls.

For an already prepared inference test environment:

```bash
PYTHON_BIN=/path/to/inference-venv/bin/python \
PYTHON_TEST_GROUP=inference deployment/ci/python-tests.sh
```

Offline tests establish the exercised contracts. They do not establish changes
in precision, recall, latency, cost or production-wide storage use; those require
appropriate fixed-corpus or workload evidence.

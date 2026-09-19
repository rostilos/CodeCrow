# CodeCrow Inference Orchestrator

A Python application for AI-powered code review using MCP (Model Context Protocol), structural repository context, and Lost-in-the-Middle protection.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Structural Repository Context Flow](#structural-repository-context-flow)
- [Task Context Flow](#task-context-flow)
- [Lost-in-the-Middle Protection](#lost-in-the-middle-protection)
- [Smart Chunking System](#smart-chunking-system)
- [Context Building Pipeline](#context-building-pipeline)
- [Token Budget Management](#token-budget-management)
- [Caching & Optimization](#caching--optimization)
- [Installation](#installation)
- [Configuration](#configuration)
- [API Reference](#api-reference)

---

## Overview

CodeCrow Inference Orchestrator is the core component responsible for:

- Receiving code review requests from the Pipeline Agent
- Extracting and analyzing Pull Request diffs
- Incorporating optional Jira/task-management context into PR-wide review
- Integrating exact structural repository context for code understanding
- Applying Lost-in-the-Middle protection for large PRs
- Orchestrating direct and agentic LLM review through one shared execution service

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CodeCrow Inference Orchestrator                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐  │
│  │   FastAPI   │───▶│   Review    │───▶│   Context   │───▶│     LLM     │  │
│  │   Server    │    │   Service   │    │   Builder   │    │   (MCP)     │  │
│  └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘  │
│         │                 │                   │                   │         │
│         │                 │                   │                   │         │
│         ▼                 ▼                   ▼                   ▼         │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐  │
│  │    RAG      │    │    File     │    │   Smart     │    │   Response  │  │
│  │   Client    │    │ Classifier  │    │  Chunker    │    │   Parser    │  │
│  └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Architecture

### Project Structure

```
inference-orchestrator/
├── main.py                    # Application entry point
├── model/
│   └── models.py              # Pydantic data models
├── llm/
│   └── llm_factory.py         # LLM provider factory
├── server/
│   ├── mcp_config.py          # MCP server configuration
│   └── web_server.py          # FastAPI HTTP server
├── service/
│   ├── review_service.py      # Core review orchestration
│   └── rag_client.py          # Structural repository-index client
├── utils/
│   ├── prompt_builder.py      # Prompt generation with L-i-M protection
│   ├── response_parser.py     # JSON response extraction
│   ├── file_classifier.py     # Neutral compatibility wrapper
│   └── diff_parser.py         # Unified diff parsing
└── requirements.txt
```

### Core Components

| Component        | Responsibility                                   |
| ---------------- | ------------------------------------------------ |
| `ReviewService`  | Orchestrates the entire review pipeline          |
| `RagClient`      | Revision-bound structural relations, graph/unit reads, and code search |
| `FileClassifier` | Neutral file-classification compatibility wrapper |
| `SmartChunker`   | Legacy neutral line/hunk chunking compatibility  |
| `PromptBuilder`  | Generates prompts with Lost-in-Middle protection |
| Shared agent execution service | Creates agent sessions from a prompt, allowed MCP tools, repository bindings, and review metadata |

### Stage 1 agent mode

The project `useMcpTools` analysis setting defaults to enabled. The Pipeline Agent
downloads the target-branch-head archive before inference and supplies a temporary
local repository binding. Every Stage 1 semantic invocation runs once through the
shared agent execution service with the complete batch schema. Its prompt contains
the diff, bounded post-change source, and parser/plugin metadata, but no preloaded
structural relation map. When the target snapshot and body-complete diff-declared overlay can form
an exact proposed tree, inference prepares one sealed generation before MCP
startup. Every eligible primary batch must first use
`getMinimalReviewContext`. `getImpactRadius`, `queryCodeGraph`,
`traverseCodeGraph`, `getStructuralUnit`, and the compatible
`exploreReviewContext` operation are focused continuations after that minimal
call. The host injects the exact batch paths, tenant/repository
identity, base/source revisions, snapshot, overlay, and selection inputs into
every structural call.
The structural service recovers the repository profile, source root, and file
selection from the sealed base generation rather than mutable live project
configuration. It clones the exactly bound sealed graph, applies the selected
changed/deleted and affected-relation delta, recomputes repository-plugin output,
and seals a complete immutable proposed generation. If plugin selection changes
or sealed repository state cannot be restored, it rebuilds the already
materialized proposed source under the inherited policy. Compatible cached
generations are reopened without rescanning the complete target snapshot. Each
graph-tool call requires the sealed review target/manifest receipt and is
read-only.
`getReviewFileContent` returns proposed source for any PR-modified path, including
a path owned by another Stage 1 batch, reports deleted or unavailable modified
paths without falling back to stale source, and reads unchanged paths from the
pinned target snapshot.
An unbounded reread of a path whose complete current source is already in the
prompt is rejected; a concrete line range remains available for omitted or
truncated context.

This request-scoped proposed-tree path depends on `useMcpTools` and global
structural-service availability. The separate project `ragEnabled` setting
controls persistent branch indexing/retrieval and does not gate the Stage 1
request-scoped proposed-tree tools.

The focused operations provide compact orientation, weighted impact radius,
paged named relation queries, bounded traversal, exact structural-unit windows,
and optional source windows with explicit source coverage. `queryCodeGraph`
accepts a non-negative result-offset `cursor` and returns `nextCursor`.
`traverseCodeGraph` defaults
to a 2,000-token response estimate (approximately serialized characters divided
by four) and returns coverage plus a frontier. `getStructuralUnit` accepts
`offset` and `maxCharacters` and returns an exact stored-content window with its
digest and continuation metadata. Graph evidence narrows the search; exact
proposed/current source remains proof. Exact source returned in any focused
tool's `sourceWindows` or by `getStructuralUnit` is used directly.
`getReviewFileContent` is the fallback for code not represented by the graph,
required whole-file bytes, or an omitted/truncated range. When
exact inputs, indexing, or graph tools are unavailable, Stage 1 remains
source-only.

Controlled Graph-RAG evaluations set `requireStructuralMcp: true`. In that mode
each Stage 1 batch must complete `getMinimalReviewContext`, `getImpactRadius`,
`queryCodeGraph`, and `getStructuralUnit` in order. The broader
`exploreReviewContext` and `traverseCodeGraph` operations remain available after
that bounded workflow, and `getReviewFileContent` remains available for concrete
source gaps. A missing sealed review generation, incomplete six-tool inventory,
MCP startup failure, failed/out-of-order required operation, or agent failure
stops the review instead of entering source-only/direct fallback. Normal requests
leave this flag false and retain the fail-open behavior.

The shared service inventories and converts MCP tools once per review, host-binds
tool arguments, and creates a fresh prompt-scoped agent from the selected cached
tools for each invocation. Stage 1 sets the initial required tool to
`getMinimalReviewContext` whenever the sealed review graph is available.
Repository evidence participates in the same review decision as the changed-file
evidence, so it can confirm or contradict a tentative local interpretation rather
than being restricted to additive findings.

An explicit `false` uses the direct structured-model path. Disabling may reduce
cost and latency, but may reduce output quality. Archive staging and repository
tools are auxiliary. If snapshot staging fails, an exact repository read can
continue through the revision-pinned provider fallback. If MCP initialization does
not produce a usable repository agent, or an agent does not return a complete
batch, Stage 1 continues through the direct structured recovery path. It may reuse
a valid exact proposed-tree response already returned to that batch, but never fetches
or preloads a target-head relation map. Explicitly disabling MCP does not silently
inject graph context.

`mcpLocalOnly` preflights only the request-bound `getReviewFileContent` proposed-
source path before model use. Structural MCP startup and graph calls remain
optional and fail open during the agent run; local-only mode still disallows the
provider-backed repository fallback and direct Stage 1 model fallback.

---

## Structural Repository Context Flow

Reviews use revision-bound structural repository context. The exact review tree
is the pinned target snapshot plus every complete non-deleted overlay body minus
deleted paths. The initial Stage 1 prompt contains no relation records. Before
MCP startup, the host prepares or reuses one content-addressed generation bound
to workspace, project, branch, base/source revisions, target/overlay hashes,
selection inputs, and structural/plugin representation. Graph queries then use
only the sealed review receipt and never mutate the generation.

The response's graph categories, source coverage, and exact bounded source
windows describe that same proposed tree. The supplied diff, Current File Content, and exact
`getReviewFileContent` results remain authoritative. Missing changed bodies,
bounded coverage, or unavailable indexing are observable optional enrichment and
do not prevent source-only review from continuing.

Stage 2 does not issue another repository search. It combines changed source,
Stage 1 findings, exact architecture/enrichment data, plugin groups, migrations,
and task context.

`/codecrow ask` may use `POST /query/code-search` when the request supplies a
complete branch/revision/generation/collection binding. Results expose
`match_reasons`; internal ordering weights are not exposed as relevance or
confidence values. Missing bindings and search
failures skip this optional context while exact VCS MCP source tools remain
available.

```python
await rag_client.get_structural_relations(
    paths=["src/auth/token.py"],
    workspace="my-org",
    project="my-repo",
    branch="main",
    repository_revision="<commit>",
    repository_generation_manifest_sha256="<receipt>",
    collection_target="<sealed-generation>",
)
```

## Task Context Flow

The Pipeline Agent may attach optional task-management context to a PR review
request under `taskContext` (or `task_context`). Jira is the current provider
used by CodeCrow task-management connections. This input is treated as untrusted
business context: prompts may use it to understand intent and acceptance
criteria, but never as instructions that override the review prompt.

Task context is controlled before the request reaches the orchestrator:

- Project config: `taskContextAnalysisEnabled` defaults to `true`.
- Disable it per project through analysis settings when task-aware review is not
  wanted.
- If disabled, no Jira/task-management lookup is attempted.
- If enabled but no unique connected task-management connection or task key is
  available, the analysis continues without `taskContext`.

```mermaid
flowchart TD
    A[Java Pipeline Agent] --> B{taskContext present?}
    B -- "no" --> C[Normal code-only review]
    B -- "yes" --> D[Stage 0 planning]
    D --> E[Task intent becomes hypotheses, not findings]
    E --> F[Stage 1 file review batches]
    F --> G[Batch prompts include task context]
    G --> H[Batch safety rule: do not report missing task requirements]
    H --> I[Stage 2 cross-file review]
    I --> J[Forced when taskContext exists]
    J --> K[Compare task intent against PR-wide change summary and all Stage 1 findings]
    K --> L[Report only evidence-backed task coverage gaps]
    L --> M[Stage 3 final summary]
```

### Why Task Coverage Runs After Batches

Stage 1 may split a PR into multiple LLM calls for token safety. A single batch
does not contain the full PR, so it must not claim that a Jira acceptance
criterion is missing. The orchestrator handles this in three places:

1. Stage 1 prompts include a batch-safety rule forbidding missing-task findings
   from one batch.
2. `should_run_stage_2()` forces Stage 2 whenever `taskContext` is present, even
   in fast-check mode.
3. Stage 2 receives the complete supplied full-PR state ledger, the current execution delta,
   architecture/plugin context and all Stage 1 findings before making
   task-coverage claims.

### Incremental Evidence Scopes

Incremental analysis does not send the whole PR through every review stage:

- Stage 0, Stage 1, hunk coverage, and new inline anchors use only `deltaDiff`.
- Stage 2 gets a locally assembled base-to-head ledger in addition to the delta.
- The ledger starts with a path manifest and then ranks task-relevant
  changed hunks without omitting lower-ranked evidence. It does not cause another source-retrieval or model
  call because `rawDiff` is already carried in the incremental request.
- If the supplied diff itself is incomplete, the ledger is marked `INCOMPLETE`;
  it is never shortened to satisfy an orchestrator character budget.

A new incremental task-coverage gap must cite a `DELTA###` excerpt that visibly
removes the task behavior. Generic or mislabelled "the PR does not implement the
task" candidates are suppressed by the host publication gate. Full-review
coverage claims require complete changed-line evidence and prompt-visible
`PRF###` references.

Successful task-aware reviews return positive evidence in the
machine-readable `taskEvidence` result field, separately from `comment`. The
Pipeline Agent validates and stores normalized evidence rows in the database
for prior same-task context in later PRs. PR and Jira comments contain only the
human-facing report and are never parsed as evidence storage. The records
contain task-relevant paths, hunk/line coordinates, and added-line excerpts—not
historical raw diffs or a model-authored completion claim.

### Request Shape

```json
{
  "projectId": 123,
  "projectWorkspace": "workspace",
  "projectNamespace": "payments",
  "projectVcsWorkspace": "org",
  "projectVcsRepoSlug": "repo",
  "pullRequestId": 456,
  "taskContext": {
    "task_key": "PAY-123",
    "task_summary": "Add refund export",
    "description": "Acceptance Criteria\n- Exports approved refunds",
    "status": "In Progress",
    "task_type": "Story",
    "priority": "High",
    "assignee": "dev@example.com",
    "reporter": "pm@example.com",
    "web_url": "https://jira.example/browse/PAY-123",
    "provider": "jira-cloud"
  }
}
```

---

## Lost-in-the-Middle Protection

Large Language Models often lose focus on content in the middle of long prompts. CodeCrow implements several strategies to combat this.

### The Problem

```
┌────────────────────────────────────────────────────────────┐
│           LLM Attention Distribution (Typical)             │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  Attention                                                 │
│      ▲                                                     │
│  100%│  ████                                    ████       │
│      │  ████                                    ████       │
│   75%│  ████                                    ████       │
│      │  ████    ░░░░░░░░░░░░░░░░░░░░░░░░░░     ████       │
│   50%│  ████    ░░░░░░░░░░░░░░░░░░░░░░░░░░     ████       │
│      │  ████    ░░░░░░░░░░░░░░░░░░░░░░░░░░     ████       │
│   25%│  ████    ░░░░░░░░░░░░░░░░░░░░░░░░░░     ████       │
│      │  ████    ░░░░░░░░░░░░░░░░░░░░░░░░░░     ████       │
│    0%└──────────────────────────────────────────────▶      │
│         START   ◀── LOST ZONE ──▶              END        │
│                                                            │
│  Problem: Critical code in middle gets less attention!     │
└────────────────────────────────────────────────────────────┘
```

### Solution: Evidence-Preserving Structuring

```
┌────────────────────────────────────────────────────────────┐
│           CodeCrow Context Structure                       │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  ████ SYSTEM PROMPT + REVIEW RULES ████              │  │
│  │  "Use all provided evidence; avoid path assumptions"  │  │
│  └──────────────────────────────────────────────────────┘  │
│                          │                                 │
│                          ▼                                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  ████ CHANGED DIFF EVIDENCE ████                     │  │
│  │  All changed files or hunk-preserving segments        │  │
│  └──────────────────────────────────────────────────────┘  │
│                          │                                 │
│                          ▼                                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  ░░░░ STRUCTURED PARSER METADATA ░░░░                │  │
│  │  Imports, symbols, calls, relationships as data       │  │
│  └──────────────────────────────────────────────────────┘  │
│                          │                                 │
│                          ▼                                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  ░░░░ PR-WIDE CONTEXT ░░░░                           │  │
│  │  Task context, changed-file list, previous issues     │  │
│  └──────────────────────────────────────────────────────┘  │
│                          │                                 │
│                          ▼                                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  ████ OPTIONAL PROPOSED-TREE TOOL SET ████           │  │
│  │  Host-bound focused queries; no prompt preload       │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### L-i-M Instructions in Prompt

```python
LOST_IN_MIDDLE_INSTRUCTIONS = """
## CRITICAL: Context Processing Instructions

This code review contains STRUCTURED CONTEXT organized by evidence source.
You MUST process all sections without assuming that a filename, extension, or
directory label determines risk.

1. **DIFF EVIDENCE**
   - Changed lines and hunk context.

2. **STRUCTURED METADATA**
   - Parser output and dependency relationships.

3. **PR-WIDE CONTEXT**
   - Task intent, changed-file list, previous issues.

4. **STRUCTURAL REPOSITORY CONTEXT**
   - In an eligible graph-backed agent, no relation map is preloaded;
     `getMinimalReviewContext` is the required initial graph call and focused
     continuations remain model-selected.
   - Batch paths and repository/revision/source-tree identity are host-bound.
     Use graph categories to narrow scope and exact related/file source to verify
     a claim. Direct recovery never fetches a target-head relation map.
   - File identity is exact or a multi-segment repository-relative suffix.
     Basenames are never identities, so repeated framework paths such as
     module-local `etc/di.xml` files remain independent.

⚠️ DO NOT skip middle sections. Each section contains unique issues.
"""
```

---

## Neutral Chunking Compatibility

`SmartChunker` remains only as a compatibility utility. It does not detect
language by extension, parse imports, classify declarations, or prioritize
chunks by code construct. Large plain text is split by line budget. Large diffs
are split by hunk budget so raw diff evidence remains intact for the LLM.

### Diff Chunking

For large diffs, hunks are kept together without classifying their contents:

```diff
# Original diff with 10 hunks

@@ -10,7 +10,7 @@ ...      ← Hunk 1
@@ -50,12 +50,15 @@ ...     ← Hunk 2
...
@@ -500,8 +510,8 @@ ...    ← Hunk 10

# SmartChunker groups hunks by token budget:
# Chunk 1: File header + Hunks 1-4
# Chunk 2: File header + Hunks 5-7
# Chunk 3: File header + Hunks 8-10
```

---

## Context Building Pipeline

### File Classification

The active review pipeline does not classify files by hardcoded path patterns.
Planning receives changed-file summaries, structured parser metadata, task
context, and relationship data, then the LLM decides review priority from that
evidence. The legacy `FileClassifier` module remains as a neutral compatibility
wrapper for older imports; it does not skip or demote paths by filename.

### Context Assembly

````python
# Stage prompts are assembled from evidence sections:
stage_1_prompt = {
    "current_file_content": "complete post-change source or complete hunk-centered windows fetched at the analyzed commit",
    "diff_evidence": "changed hunks or hunk-preserving large-diff segment",
    "structured_parser_metadata": "raw parser metadata JSON",
    "task_context": "untrusted task/acceptance criteria text",
    "structural_tools": "host-bound minimal context, impact, query, traversal, unit, and compatible exploration; optional per batch",
    "review_file_tool": "getReviewFileContent selects proposed source for modified paths and pinned target source for unchanged paths",
    "degraded_structural_context": "reuse only an exact proposed-tree response already returned to this batch; never fetch a target-head map",
}

# Python does not infer file priority or risk from path/extension/category.
# The LLM receives evidence and decides what matters.
````

---

## Token Budget Management

Stage 1 keeps structurally related files together while the combined diff fits
the model budget. Large file diffs are split into hunk-preserving segments only
when the evidence itself exceeds that budget.

### Evidence Distribution

```
┌────────────────────────────────────────────────────────────┐
│             Stage Prompt Evidence Sections                 │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  ┌────────────────────────────────────────────────────┐    │
│  │  CURRENT FILE CONTENT (POST-CHANGE)                │    │
│  └────────────────────────────────────────────────────┘    │
│                                                            │
│  ┌────────────────────────────────────────────────────┐    │
│  │  DIFF EVIDENCE                                     │    │
│  └────────────────────────────────────────────────────┘    │
│                                                            │
│  ┌────────────────────────────────────────────────────┐    │
│  │  STRUCTURED PARSER METADATA                        │    │
│  └────────────────────────────────────────────────────┘    │
│                                                            │
│  ┌────────────────────────────────────────────────────┐    │
│  │  TASK / PR-WIDE CONTEXT                            │    │
│  └────────────────────────────────────────────────────┘    │
│                                                            │
│  ┌────────────────────────────────────────────────────┐    │
│  │  BOUNDED PROPOSED-TREE RELATION BRIEFING         │    │
│  │  + OPTIONAL FOCUSED GRAPH / SOURCE TOOLS          │    │
│  └────────────────────────────────────────────────────┘    │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### Dynamic Adjustment

Stage 1 batch size is controlled by environment settings such as
`REVIEW_STAGE1_BATCH_TOKEN_BUDGET`, `REVIEW_STAGE1_DIFF_CHUNK_TOKEN_BUDGET`,
`REVIEW_STAGE1_MAX_PARALLEL`, the source/metadata character limits, and bounded
MCP tool-result limits.
Before MCP startup, the host prepares one sealed proposed-tree generation when
the exact binding is available. The first eligible Stage 1 turn is the required
bounded `getMinimalReviewContext` call; impact, broader-context, query,
traversal, and unit tools continue only from its named IDs and frontier. Graph
failure is observable and fail-open, so
the supplied diff/current source still receives a normal review.
`REVIEW_STAGE1_MAX_PARALLEL` limits batches inside one review, while
`MAX_CONCURRENT_REVIEWS` limits reviews admitted by one orchestrator process.
The direct streaming `/review` compatibility route emits review events but no
separate transport heartbeat in this release. Configure proxy idle timeouts
above the longest expected queue/provider wait. Disconnecting the client
cancels its request runner.
`REVIEW_STAGE1_AGENT_TIMEOUT_SECONDS=600` is a separate wall-clock
deadline around one repository-agent batch, including all of its model turns and
tool calls. Expiration cancels that agent and enters the existing direct
structured recovery path when local-only execution is not required.
Complete changed hunks remain primary evidence. When the selected review-size
profile reaches its Stage 1 invocation ceiling, lower-priority units are recorded
as `budget_omitted`; omitted hunk/unit/context counts are carried into terminal
coverage and are not interpreted as negative evidence.

Normal review stages do not add a CodeCrow-generated output/completion cap.
Provider-native context or completion limits still apply, and a provider adapter
may supply a limit when its API requires one. Input packing remains independent
of generated output: it keeps complete evidence records within each request's
available context window.

Primary semantic invocation ceilings are also size-specific: Stage 1 total
4/12/24 with at most 1/2/3 units for one hunk-owned item, Stage 2 1/2/4 packets,
Stage 3 1/2/4 child calls plus at most one final synthesis, and branch
reconciliation 1/2/4 packets. Structured primary calls retain the stage's
configured reasoning effort. When a response is exhausted or invalid, a stage
that supports retry may make one direct-output retry with reasoning disabled.
Packet/shard omission counts are prompt-visible and preserved in provenance.
When MCP tools are enabled, each Stage 1 semantic invocation uses one agent run
capped at six medium-reasoning repository-capable model calls. A graph-eligible
invocation must spend the first four on minimal context, impact radius, a named
graph query, and exact structural-unit inspection; the remaining calls are
available for focused graph continuations or file reads. A graph-ineligible
source-only invocation may return a complete response without a tool call. The
shared agent runtime then provides up to two separate
calls for schema synthesis and one possible correction, with repository tools
disabled while the final batch object is emitted or corrected. OpenRouter agent
turns merge `provider.require_parameters=true` with the model and request's
existing provider route preferences so terminal schema tool choices use a
compatible endpoint. Routed OpenRouter calls do not add the optional
`parallel_tool_calls` control: otherwise an ordered endpoint that does not
advertise that parameter would be excluded before routing. Competing schema and
out-of-inventory calls remain rejected locally. If a provider returns an out-of-inventory
call, the graph removes that response before its static tool node and reissues
the narrowed request. The rejected tool is not executed and the retry consumes
the existing repository-call allowance. Exact JSON from the provider is
validated locally; when the
schema has one explicitly list-typed field, a valid root JSON array is wrapped
into that field without another model call. A correction that repeats a
repository tool name ends before the graph's static tool node can execute it or
request another model call, and mcp-use's `No output generated`
sentinel is not accepted as structured output. If the run still does not produce
a complete batch, direct recovery may reuse a valid exact proposed-tree response
already returned to it; it never fetches or preloads a target-head map.
Disabling MCP tools uses the direct structured invocation immediately.

The proposed-tree structural generation is content-addressed and bound by tenant,
repository, base/source revisions, target/overlay hashes, selection inputs, and
structural/plugin representation. It is materialized as target snapshot plus all
changed bodies declared by the acquired raw unfiltered base-to-head diff, minus
declared deletions, while only changed files accepted by the sealed base
selection become structural overlay facts. Missing declared bodies disable proposed-tree graph
operations; a path silently omitted by the upstream diff cannot be proven
complete here. Optional structural retrieval failures
leave the review running source-only with changed source, diff evidence,
enrichment, and plugin context.

Final issue deduplication begins with exact local root identities and uses grouped
model-assisted dedup by default (`REVIEW_LLM_DEDUP_ENABLED=true`). The host sends only
ambiguous candidate components; unrelated singletons consume no dedup tokens.
The model returns explicit duplicate groups, and only `HIGH`-confidence decisions
inside one host-selected component can merge findings. Omitted, uncertain,
malformed, or failed decisions retain all ambiguous findings. Category or
severity drift does not split a proven root cause. Cross-file consolidation is
allowed only for one shared causal defect and retains every secondary `file:line`
in the finding's reason and `relatedLocations`.

Candidate components use the shared 60k estimated-input target and the request's
model-context value with a generation reserve. Whole candidate groups are packed
without field-level clipping. If one indivisible group cannot fit, no oversized
request is sent and every finding in that group is retained.
`REVIEW_DEDUP_MAX_PARALLEL=4` limits candidate batches within one review and does
not serialize concurrently admitted reviews. Set `REVIEW_LLM_DEDUP_ENABLED=false`
to use exact local merging only.

The Java persistence boundary adds an exact same-file substantive-title/reason
safety net before its exact category-aware and category-agnostic anchored
fingerprints. It retains the strongest source anchor, highest severity, best
valid fix, and additional concrete locations. Short boilerplate, sharing only a
file/line/category/FILE scope, and placeholder fingerprints without an actual
anchor cannot suppress another finding. These Java checks add no model calls.

Full-pipeline prompt dry runs forward the normal Stage 0–3 progress events and
persist a compact `review_evidence_completed` record with terminal hunk counts,
registered/completed review-unit counts, deterministic retrieval states, and
exact evidence counts. The artifact also binds those diagnostics to the target
and source branches, immutable head/base object IDs, changed/deleted manifests,
and raw-diff digest. Review-scoped plugin diagnostics are captured without
cross-talk between concurrent reviews. `tools.review_quality.prompt_dry_run_audit`
fails closed when any general pipeline stage, exact coverage state, retrieval
state, plugin diagnostic, prompt budget, or review-provider guard is missing.
Review-scoped assembly diagnostics record per-batch character allocations for
diff, current source, AST metadata, structural context, plugin context, project rules, task
context, and prior issues; they add nothing to the prompt itself. These
diagnostics do not add model calls and are not precision/recall measurements.

Queued review progress is serialized in callback order, and the consumer awaits
the single terminal publication after all Stage 0–3 events. While any normal or
provider-free review is active it emits `state=processing` every
`ANALYSIS_QUEUE_HEARTBEAT_SECONDS` (default 30). The Java producer uses
`ANALYSIS_QUEUE_INACTIVITY_TIMEOUT_MINUTES` (default 15) as a silence deadline,
not a total-runtime limit, and renews the PR analysis lock on those liveness
events. Missing/expired ownership or a renewal exception fails the review before
persistence or VCS reporting.

Provider webhook commit abbreviations never become review identity. The Java
producer accepts only an exact full object ID or a matching prefix of at least
12 characters as an acquisition hint, fetches full base/head IDs from the VCS
PR endpoint, and replaces the hint before persistence, RAG, or this service.
Malformed provider IDs and stale/mismatched hints fail before model work.

Stage 1 persists per-batch agent telemetry independently of the finding result:
shared generation preparation, required-first-call compliance, tool sequence,
bound graph revision, result/source counts, bounded/whole/rejected source reads,
evidence use, latency, degradation, and partial failures. Tool
events emitted before an agent failure remain observable, so a recovered direct
batch does not erase what the agent already attempted.

Actual candidate-generation evidence is available through an operator-only,
project-scoped capture wrapper. `REVIEW_QUALITY_CAPTURE_ENABLED=true` also
requires a non-empty numeric `REVIEW_QUALITY_CAPTURE_PROJECT_IDS` allowlist.
The wrapper records the sanitized immutable request, exact model inputs,
schemas/tools, actual responses, usage metadata, and final result under
`REVIEW_QUALITY_CAPTURE_OUTPUT_DIR` (default
`/app/logs/review-quality-captures`). It observes the existing BYOK calls and
does not add or retry them. Artifacts contain proprietary source/context and use
mode `0600`; credential values are redacted. `REVIEW_QUALITY_CAPTURE_MAX_FILES`
defaults to 20. Initialization or atomic persistence failure fails the selected
review closed so an incomplete artifact cannot be mistaken for complete quality
evidence. Capture and runtime telemetry show which calls and tools were observed;
they do not prove a precision, recall, cost, or latency improvement, and an
artifact missing provider callbacks cannot prove a tool was unavailable.

---

## Installation

### Requirements

- Python 3.10+
- Structural repository-index service running (optional enrichment)
- MCP Server JAR file

### Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Development/test environment (includes the runtime requirements)
pip install -r requirements.test.txt

# Configure environment
cp .env.sample .env
# Edit .env with your settings
```

---

## Configuration

### Environment Variables

```bash
# Server Configuration
AI_CLIENT_HOST="0.0.0.0"
AI_CLIENT_PORT="8000"

# MCP Server
MCP_SERVER_JAR="/path/to/mcp-server.jar"
# Total deadline for one MCP tool call, including per-connection queue wait.
# A timed-out optional Stage 1 tool returns an error and review continues from
# the diff/current-source evidence.
MCP_TOOL_CALL_TIMEOUT_SECONDS="120"

# LLM tuning (provider credentials are supplied with each Java request)
LLM_TEMPERATURE="0.0"
# OpenRouter, OpenAI, and custom OpenAI-compatible clients. The timeout applies
# to each SDK request attempt; max retries counts attempts after the first.
LLM_PROVIDER_TIMEOUT_SECONDS="120"
LLM_PROVIDER_MAX_RETRIES="1"

# Whole repository-agent batch deadline, independent of the full review timeout.
REVIEW_STAGE1_AGENT_TIMEOUT_SECONDS="600"

# Structural repository index
RAG_ENABLED="true"
RAG_API_URL="http://codecrow-rag-pipeline:8001"
# Independent timeout for each optional proposed-tree operation. It does not
# inherit the full review timeout; timeout returns a tool error and Stage 1
# continues from the diff/current source.
RAG_REVIEW_CONTEXT_TIMEOUT_SECONDS="120"
# One review-scoped generation preparation before MCP startup.
RAG_REVIEW_PREPARATION_TIMEOUT_SECONDS="600"
```

The local deployment sample uses a 7,200-second preparation timeout and a
10,800-second full-review timeout for long benchmark graph builds. The
application defaults above remain unchanged; the full-review ceiling must exceed
preparation plus model execution.

---

## API Reference

### POST /review

Submit a code review request.

**Request:**

```json
{
  "projectId": 123,
  "projectWorkspace": "workspace",
  "projectRepoSlug": "repo-name",
  "aiProvider": "openrouter",
  "aiModel": "anthropic/claude-3-5-sonnet",
  "aiApiKey": "your-api-key",
  "pullRequestId": 456,
  "taskContext": {
    "task_key": "PAY-123",
    "task_summary": "Add refund export",
    "description": "Acceptance Criteria\n- Exports approved refunds"
  },
  "ragEnabled": true
}
```

**Response:**

```json
{
  "result": {
    "comment": "Review summary with findings...",
    "issues": {
      "0": {
        "severity": "HIGH",
        "file": "src/auth/service.py",
        "line": "42",
        "reason": "SQL injection vulnerability in user query",
        "suggestedFix": "Use parameterized queries..."
      }
    }
  },
  "error": null
}
```

### GET /health

Health check endpoint.

**Response:**

```json
{
  "status": "healthy",
  "rag_enabled": true,
  "rag_healthy": true
}
```

---

## Related Documentation

- [RAG Pipeline Documentation](../rag-pipeline/README.md)
- [MCP Scaling Strategy](../../docs/architecture/mcp-scaling-strategy.md)
- [Integration Guide](../rag-pipeline/docs/INTEGRATION_GUIDE.md)

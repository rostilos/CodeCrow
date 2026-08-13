# CodeCrow Inference Orchestrator

The inference orchestrator executes evidence-bound code reviews. The Java
Pipeline Agent owns VCS acquisition, immutable PR identity, persistence, and
cross-run issue lineage. This service owns current-run discovery, exact-context
follow-ups, verification, and rendering.

## Contents

- [PR review runtime](#pr-review-runtime)
- [Evidence and context](#evidence-and-context)
- [Incremental reviews and issue history](#incremental-reviews-and-issue-history)
- [Runtime limits and failure behavior](#runtime-limits-and-failure-behavior)
- [Project structure](#project-structure)
- [Installation](#installation)
- [Configuration](#configuration)
- [API](#api)

## PR review runtime

The active PR path is:

```text
Java: exact base/head + complete path manifest + diff/current source
                                  |
                                  v
Python: validate snapshot and acquired diff manifest
                                  |
                                  v
optional complete current-head PR overlay
                                  |
                                  v
host-owned hunk ledger and mandatory review units
                                  |
                                  v
Stage 0 LLM annotations (risk, focus, bounded hypotheses only)
                                  |
                                  v
Stage 1 parallel review units + at most one exact continuation per batch
                                  |
                                  v
targeted cross-file investigations from admitted exact relationships
                                  |
                                  v
provenance, deterministic evidence, and plugin gates
                                  |
                                  v
exact-only merge + one packetized verification wave
                                  |
                                  v
deterministic report renderer
                                  |
                                  v
Java: persist findings and reconcile all-run PR lineage
```

There is no broad repository-wide Stage 2 sweep, model-based semantic dedup
stage, Stage 3 model aggregation, or iterative MCP verifier in this PR path.
Legacy helpers with those names may remain for branch, QA, or source
compatibility, but they do not determine PR finding membership.

### 1. Snapshot and manifest validation

The request carries the exact reviewed revision and separate review/context
scopes:

- `changedFiles`, `deletedFiles`, and `rawDiff` or `deltaDiff` describe the
  hunks that this execution must review.
- `fullPrChangedFiles` and `fullPrDeletedFiles` describe the current
  base-to-head PR state used for context maintenance.
- `pullRequestFileManifest` carries provider-native add, modify, delete, and
  rename identities plus a completeness receipt.
- `baseCommitHash` and `currentCommitHash` bind reads and RAG generations to the
  immutable PR snapshot.

The orchestrator validates the acquired diff against the review manifest before
model work. Provider metadata is authoritative, but an unavailable optional
context source does not turn a review into a clean result.

### 2. Complete current-head overlay

RAG is optional supporting context. The orchestrator mutates a PR overlay only
when the provider attests a complete base-to-head manifest and exact
current-head content is available for every active source path. Deleted and
renamed-away paths are sent as tombstones so stale target-branch chunks cannot
reappear through the overlay.

A usable overlay is bound to the target generation, base and head revisions,
plugin identities, generation fingerprint, and overlay membership digest. RAG
may return `reused` only when those receipts still match. If the manifest is
partial, current source is missing, indexing fails, or the generation receipt
is incomplete, the overlay is not rebound; review continues with the diff,
enriched current source, and any compatible target-branch context. Partial diff
text is never indexed as if it were a complete file.

### 3. Host-owned planning and hunk coverage

The diff processor assigns every acquired hunk either a deterministic
non-reviewable disposition or a stable reviewable hunk ID. The host then creates
the mandatory Stage 1 plan. Stage 0 can annotate that plan with risk, focus
areas, and path-bound cross-file hypotheses, but it cannot remove files, skip
hunks, or redefine coverage.

Large diffs are split into hunk-preserving review units. If a single hunk must
be fragmented for the prompt budget, all fragments retain ownership of the
original hunk. The review completes only after every registered mandatory unit
has a terminal completion record. An invalid or missing Stage 1 batch result is
an incomplete analysis, not evidence that the batch is clean.

### 4. Stage 1 discovery and exact follow-ups

Each Stage 1 unit receives the changed hunk, bounded post-change source,
structured parser/plugin evidence, project rules, untrusted task context, and
compatible RAG evidence. Diff hunks and exact current source are primary;
semantic chunks are supporting evidence and navigation hints.

Stage 1 may return explicit context requests:

- `LOCAL_EXACT` requests are resolved from enriched current source first, then
  by a revision-pinned exact read. RAG/parser data may locate a path, symbol, or
  range but cannot supply the final exact evidence.
- A batch can receive one continuation containing the resolved evidence. If a
  request is unresolved, repeated, or outside the shared follow-up budget,
  findings that depend on it are withheld while unrelated findings remain.
- `CROSS_FILE` requests are recorded with their originating changed unit. They
  are not publishable findings by themselves.

The MCP client is used as an exact-read transport when necessary. The active PR
pipeline does not give an agent an open-ended MCP tool loop and does not use MCP
to iteratively rewrite verification decisions.

### 5. Targeted cross-file investigation

Cross-file work is evidence-triggered rather than a mandatory whole-repository
model pass. A ticket is admitted only when a changed trigger has a falsifiable
hypothesis and an exact relationship to another path. Sources include
host-bound Stage 1 requests, parser/plugin relationships, path-specific planner
hypotheses, a current candidate's causal path, and visible contract changes.

Both sides of the relationship must resolve to exact current-head source. Each
ticket includes bounded trigger hunks and current source and may emit at most one
candidate anchored to a changed line. Missing source, an ambiguous edge, or an
exhausted follow-up budget makes the ticket incomplete and dependent claims are
withheld. This stage does not perform generic semantic duplicate search or
speculative architecture scanning.

Deletion-only and metadata-only rename PRs use a narrower compatibility path
because they have no truthful current-side inline anchor. The host binds at most
four units to real deleted hunks or provider rename receipts, locates remaining
consumers through revision-bound navigation, and accepts only exact current-head
source as proof. A proven break is emitted as a file-level summary issue at line
zero and still passes the common verifier. If exact related source cannot be
resolved, no compatibility model call is made and no finding is fabricated.

### 6. Final gates, verification, and rendering

All Stage 1 and targeted cross-file candidates follow the same terminal path:

1. The candidate ledger proves which prompt, review unit, hunks, and visible
   evidence produced each candidate.
2. Deterministic evidence and plugin gates reject non-actionable, stale,
   unanchored, or unsupported candidates.
3. Exact causal-evidence fingerprints merge only identical roots. Similar
   wording, category, severity, file/line proximity, or model opinion is not
   enough to merge findings.
4. The verifier receives packetized candidates with exact current-source
   windows, changed-hunk evidence, and ledger evidence. It performs one
   verification wave and returns `CONFIRMED`, `REJECTED`, or `INCOMPLETE` per
   verification ID.
5. Rejected and incomplete candidates are withheld. Provider or packet failure
   does not publish an unverified candidate.
6. `report_renderer.py` sorts the confirmed structured findings and formats the
   report deterministically. It cannot add, remove, merge, or rewrite findings.

Task-management text may inform Stage 0 and Stage 1 hypotheses, but it is
untrusted context and cannot override review rules. A file batch must not infer
that a PR-wide acceptance criterion is absent. Positive `taskEvidence` is built
separately from bounded host evidence and returned for Java persistence; it is
not parsed from the human report.

## Evidence and context

The active evidence order is:

1. Acquired changed hunks and host hunk identity.
2. Exact post-change source at the reviewed head.
3. Structured parser and plugin facts tied to the snapshot.
4. Exact target-generation or complete PR-overlay RAG context.
5. Task and PR metadata as untrusted intent context.

Repository paths are normalized identities. A basename alone is never accepted
when it could refer to multiple repository paths. Exact-source ambiguity remains
unresolved instead of choosing one candidate silently.

Semantic RAG filler and duplication-search RAG are disabled by default. When
enabled, they remain supplemental and cannot replace exact source. LLM reranking
is also optional; disabling it preserves the retrieved evidence and removes the
extra reranking call.

## Incremental reviews and issue history

Java selects `INCREMENTAL` only when the predecessor is an accepted review for
the same PR, base commit, and analysis-behavior digest, and its head is proven to
be an ancestor of the new head. A changed base, missing commit, incompatible
behavior, unavailable ancestry, or any ambiguous lineage forces `FULL`.

Production PR result-cache shortcuts are disabled. Every newly accepted webhook
or comment-command analysis reaches context maintenance and inference, including
an intentional rerun of the same head. Only recovery or redelivery of the same
persisted outer job at the same confirmed base/head and behavior is idempotent.

In incremental mode, Stage 0, Stage 1, hunk coverage, and publication anchors
use the delta. The complete base-to-head manifest and source scope still maintain
the current-head overlay and bounded PR evidence. Previous issue prose is not
used to manufacture new discovery candidates.

After Python returns the freshly verified findings, Java performs destination-PR
lineage reconciliation against active tips derived from every earlier run, not
only the immediately preceding analysis. Matching uses exact persisted path and
source-anchor receipts; category and severity are not identity fields. Each
historical tip can match at most one current finding, ambiguous matches start a
new root, and omission never resolves a historical issue.

## Runtime limits and failure behavior

The active defaults are intentionally small in number and apply to prompt or
provider-call packing, not to silent file skipping:

| Area | Active defaults |
| --- | --- |
| Stage 0 annotations | `REVIEW_STAGE0_PLANNING_CHAR_BUDGET=120000` |
| Stage 1 batching | 7 files, 60,000 input tokens, 35,000 tokens per diff unit, 5 parallel batches |
| Stage 1 current source | 12,000 characters per file, 48,000 characters per batch |
| Shared discovery follow-ups | `REVIEW_FOLLOW_UP_MAX_CALLS=4` |
| Exact current-head reads | `REVIEW_EXACT_SOURCE_MAX_READS=24`, single-flight per source window |
| Targeted cross-file | 8 tickets, 120,000 characters per evidence pack |
| Deletion/rename compatibility | 4 changes, 3 exact related sources per change, 60,000 prompt characters |
| Verification | 8 candidates per call, 4 calls, 120,000 characters per packet |

Corresponding environment variables are listed in
`deployment/config/inference-orchestrator/.env.sample`. The current output caps
are:

| Active model step | Small | Medium | Large |
| --- | ---: | ---: | ---: |
| Stage 0 annotations | 4,000 | 4,000 | 4,000 |
| Stage 1 discovery | 20,000 | 30,000 | 40,000 |
| Targeted cross-file (`stage_2` compatibility key) | 6,000 | 6,000 | 6,000 |
| Verification | 3,000 | 3,000 | 3,000 |

There are no active PR-review output caps for semantic dedup or Stage 3 because
those model calls are not part of this path.

Optional RAG, semantic retrieval, reranking, and exact-context enrichment fail
open to the strongest compatible local evidence, but a claim that explicitly
depends on missing evidence is withheld. Snapshot mismatch, incomplete mandatory
hunk coverage, malformed Stage 1 coverage, or an unaccounted candidate fails the
review rather than producing a misleading clean result.

Queued progress is emitted in callback order. Processing heartbeats keep the
Java-side inactivity lease alive; the producer's inactivity timeout is a silence
deadline, not a total review-duration limit.

## Project structure

```text
src/
├── api/                         # FastAPI application and routes
├── llm/                         # Provider construction and transport guards
├── model/                       # Request and structured-output contracts
├── server/                      # Queue and stdin consumers
├── service/
│   ├── rag/                     # RAG client and optional reranker
│   └── review/
│       ├── review_service.py    # Request lifecycle and evidence scopes
│       ├── candidate_ledger.py  # Candidate provenance and terminal accounting
│       └── orchestrator/
│           ├── orchestrator.py
│           ├── stage_0_planning.py
│           ├── stage_1_file_review.py
│           ├── exact_context.py
│           ├── targeted_cross_file.py
│           ├── verification_wave.py
│           └── report_renderer.py
├── utils/                       # Diff, path, prompt, and MCP utilities
└── main.py
```

## Installation

Requirements:

- Python 3.10+
- the CodeCrow MCP server JAR for the standard review runtime;
- the RAG Pipeline only when RAG is enabled.

```bash
pip install -r requirements.txt
pip install -r requirements.test.txt
cp ../../../deployment/config/inference-orchestrator/.env.sample .env
```

## Configuration

```bash
AI_CLIENT_HOST="0.0.0.0"
AI_CLIENT_PORT="8000"
MCP_SERVER_JAR="/path/to/codecrow-mcp-servers.jar"
LLM_TEMPERATURE="0.0"
RAG_ENABLED="true"
RAG_API_URL="http://rag-pipeline:8001"
```

Provider credentials and the selected model are supplied per request by Java.
Review-result reuse is not performed in this service; only exact RAG generations
may be reused when their immutable receipts match.

## API

### `POST /review`

The standard request includes Java-owned snapshot and manifest evidence:

```json
{
  "projectId": 123,
  "projectWorkspace": "workspace",
  "projectNamespace": "payments",
  "projectVcsWorkspace": "org",
  "projectVcsRepoSlug": "repo",
  "aiProvider": "openrouter",
  "aiModel": "anthropic/claude-sonnet",
  "aiApiKey": "request-scoped-secret",
  "pullRequestId": 456,
  "targetBranchName": "main",
  "sourceBranchName": "feature/refunds",
  "baseCommitHash": "0123456789abcdef0123456789abcdef01234567",
  "currentCommitHash": "89abcdef0123456789abcdef0123456789abcdef",
  "analysisMode": "FULL",
  "changedFiles": ["src/refunds.py"],
  "deletedFiles": [],
  "fullPrChangedFiles": ["src/refunds.py"],
  "fullPrDeletedFiles": [],
  "pullRequestFileManifest": {
    "completeness": "COMPLETE",
    "receipt": "provider-receipt",
    "changes": [
      {"path": "src/refunds.py", "previousPath": "", "kind": "MODIFIED"}
    ]
  },
  "fullPrManifestComplete": true,
  "rawDiff": "diff --git a/src/refunds.py b/src/refunds.py\n...",
  "ragEnabled": true,
  "taskContext": {
    "task_key": "PAY-123",
    "task_summary": "Add refund export"
  }
}
```

The response contains a deterministic human report, confirmed structured
`issues`, and optional machine-readable `taskEvidence`. Java owns subsequent
persistence, exact deduplication safeguards, VCS reporting, and all-run lineage.

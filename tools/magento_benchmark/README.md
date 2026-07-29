# CodeCrow Magento 2 review benchmark

This module builds and evaluates a reproducible benchmark from historical
`magento/magento2` pull-request review rounds. It freezes the code reviewers
actually saw, recreates that base/head pair in a fork, runs CodeCrow against an
already indexed base revision, matches CodeCrow findings to curated reviewer
issues, and produces auditable metrics plus a static dashboard.

The checked-in [corpus draft](data/README.md) is **provisional and unscored**.
It contains 50 merged PR checkpoints and 121 provisional root review comments,
distributed as 40 small (3–10 files), 7 medium (11–30), and 3 large (31–80).
Those comments have mechanical anchor evidence, but not the semantic and fix
adjudication required for a released corpus. Do not calculate or publish
precision, recall, F1, FP, or FN from `data/corpus-draft.json`.
No raw GitHub source archive, authenticated thread-evidence artifact, curation
packet, or released labels are committed with this draft; they must be
collected and digest-bound before it can leave provisional status.

The benchmark protocol is defined in [METHODOLOGY.md](docs/METHODOLOGY.md).
Requirements for a defensible paper run are in
[TECHNICAL_PAPER_PROTOCOL.md](docs/TECHNICAL_PAPER_PROTOCOL.md).

## Install

Python 3.11 or newer and Git are required. GitHub collection and replay need
network access. Running CodeCrow needs the deployed CodeCrow services and
Docker CLI access when the production Redis transport is selected.

```bash
cd tools/magento_benchmark
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
cp config.example.toml config.toml
magento2-benchmark --help
```

The package metadata uses the fixed PEP 621 sentinel `0+unreleased` only so an
editable install has valid distribution metadata. It is not a module release
or compatibility version, and the benchmark does not define a release line.

Run all commands from this directory in the examples below. Generated
credentials must never be written into `config.toml`, a corpus, or a run
artifact. Configuration records only the names of secret environment
variables:

```bash
export GITHUB_TOKEN=...
export OPENROUTER_API_KEY=...
export CODECROW_SERVICE_SECRET=...
```

`GITHUB_TOKEN` is optional for public, unauthenticated REST reads until GitHub's
rate limit is reached. It is required for authenticated GraphQL thread
collection and by `replay-apply`. The analysis and judge API keys are read from
the environment variables named by `analysis.api_key_env` and
`judge.api_key_env`. `CODECROW_SERVICE_SECRET` is required for the internal
CodeCrow finalization and RAG calls.

## End-to-end workflow

### 1. Verify and inspect the provisional draft

The draft is an evidence inventory, not the released corpus schema.

```bash
(cd data && sha256sum --check corpus-draft.sha256)
magento2-benchmark validate-draft \
  --draft data/corpus-draft.json
jq '.actual_distribution, .case_count' data/corpus-draft.json
```

`validate-draft` checks the draft-only 50-case invariants and reports
`paperReady: false` and `scoringEnabled: false`. It is distinct from
`validate`, which accepts the materialized released-corpus format.

For a new candidate search, `discover` caches recent root, right-side, human
inline comments without deciding that they are valid issues:

```bash
magento2-benchmark --config config.toml discover \
  --pages 10 \
  --output work/discovery.json
```

The discovery artifact is a sealed candidate-pool record, not just a ranked
list. It retains every raw REST page, the canonical
`GET /repos/magento/magento2/pulls/comments` path, exact sorted query and page
parameters, safe representation headers, status, ETag/Last-Modified values when
available, response digests, and a prior-page digest chain. It also freezes a
versioned rejection/grouping policy, the actionable-word ranking hint, the
candidate tie-break order, rejection counts, the raw-population digest, and the
ordered candidate-set digest. Validation reconstructs the complete candidate
list from the raw pages; changing and re-sealing a query, page link, policy,
candidate, or candidate order still fails.

`sourceMode` is `live` for an online or conditionally revalidated run and
`cache-only` for explicit offline input. Credentials and conditional request
headers are intentionally not stored. The actionable-word count is only a
deterministic triage hint and is never evidence that a comment is valid gold.
Create the standalone selection binding after the ordered draft or released
selection exists:

```bash
magento2-benchmark link-discovery-selection \
  --discovery work/discovery.json \
  --selection work/selection.json \
  --output work/discovery-selection-link.json
```

The command validates both inputs and binds the ordered selection to the
discovery digest, entire candidate-set digest, exact PR/H candidate, and
selected comment IDs. The checked-in draft predates this binding and its filename-only
`sampling_provenance` is not immutable page evidence; no exhaustive-sampling or
selection-rate claim may be made from it.

`--offline` permits cache-only discovery, source archival, materialization, and
thread collection. A missing API cache entry is an error; offline mode never
silently substitutes stale or incomplete data. Offline materialization also
requires an existing normal clone or linked worktree containing every exact
selected object. It never clones, fetches, changes `origin`, or permits
partial-clone lazy fetching; shallow history, legacy grafts, and missing local
objects fail explicitly. Cached
GraphQL thread pages can be consumed offline without a token, but their cached
request and response digests must match exactly and a cache miss fails without
a network fallback. A live GraphQL request still requires a token. Conversely,
a live request that cannot reach GitHub fails even when a cache entry exists;
select `--offline` explicitly when a cache-only evidence run is intended.

### 2. Archive source evidence and collect review conversations

Before spending the larger request budget needed for the full source archive,
the selected current comment bodies and complete REST reply sets can be checked
with one paginated review-comment request per PR:

```bash
magento2-benchmark --config config.toml verify-current-comments \
  --draft data/corpus-draft.json \
  --output work/current-comments-attestation.json
```

The command stores the exact raw REST pages, request URLs, ETags when returned,
selected-root response digests, and all direct replies for every one of the 121
selected roots. The artifact shape is defined by
[`current-comment-attestation.schema.json`](schemas/current-comment-attestation.schema.json).
It fails on body, timestamp, reviewer, submitted-review ID,
path, original commit, or current/original line-range drift. `--offline`
recomputes the same bindings from the explicit GitHub cache without attempting
network access and labels the artifact `cache-only`; because an old cache
cannot establish when GitHub last served the objects, that mode must not be
described as a live observation.

This attestation is deliberately provisional. It does not contain the full raw
pull-request and submitted-review responses, authenticated GraphQL
resolution/outdated metadata, or human semantic/fix decisions, so it cannot
enable scoring or replace the paper-ready source archive and thread evidence.

First archive the exact REST inputs from which the pinned draft was derived:

```bash
magento2-benchmark --config config.toml archive-draft-sources \
  --draft data/corpus-draft.json \
  --output work/draft-source-archive.json
```

This captures every source PR, all inline review comments, submitted reviews,
and the selected comments, with per-case and archive digests. It fails if a
selected comment's review link, author/type, body, association, timestamps,
canonical PR discussion URL, path, diff hunk, current/original commit, side,
line/position fields, or root status has drifted from the draft. A linked
review must be a human `User` review by the same reviewer, have state
`APPROVED`, `CHANGES_REQUESTED`, `COMMENTED`, or `DISMISSED`, have a valid
`submitted_at` and full commit SHA, and name that exact PR in
`pull_request_url`. `PENDING`, unsubmitted, and cross-PR reviews fail closed.
The checked-in repository does not contain this raw archive; generating and
preserving it is a paper-release prerequisite.

Release retains a `sourceArchiveEvidence` record for every case. It contains
the top-level `archiveDigest`, the case's `caseEvidenceDigest`, and SHA-256
digests of the exact raw PR response (`pullResponseSha256`), each selected
review-comment response (`selectedCommentResponseSha256`), and each referenced
submitted-review response (`submittedReviewResponseSha256`). Materialization
refetches those objects, recomputes every object digest, and carries the same
case record into the corpus. Paper-ready validation then binds the embedded raw
responses and normalized gold fields back to those digests. Missing, extra,
malformed, or changed evidence is an error rather than a provisional match.

This archive pins the **currently returned** REST body and `updated_at`; it does
not reconstruct prior edits. In the checked-in draft, 119 of 121 selected
comments have `updated_at != created_at`. GitHub's collected records therefore
cannot prove that their current wording is the exact wording that existed at
H. Curators must validate the current text against the H code, complete thread,
and fix evidence, and a paper must disclose the unavailable edit history.

Then collect every selected thread and submitted review:

```bash
magento2-benchmark --config config.toml collect-threads \
  --draft data/corpus-draft.json \
  --output work/thread-evidence.json
```

With `GITHUB_TOKEN` set, this command combines authenticated, paginated GraphQL
review threads with REST comments and submitted reviews. It records
`isResolved`, `isOutdated`, path/line, complete GraphQL message lists, source
digests, and a raw page archive inside `thread-evidence.json`. Every page
retains the canonical endpoint, POST query and exact cursor variables, response
object, prior-page link, and deterministic request/response/page digests. The
archive seals the ordered page count. Release checks the null first cursor,
every `endCursor`→`after` handoff, and final page, then binds each normalized
selected thread to its exact raw node/page. Preserve `thread-evidence.json`;
the GitHub cache remains useful for repeat collection but is not the only copy
of the raw evidence.

In live mode, no token means the command falls back to REST and records no
trusted resolution metadata. That artifact can support provisional curation,
but cannot pass `release-selection --paper-ready`. In explicit offline mode,
tokenless collection instead consumes the exact cached GraphQL pages; a missing
or digest-invalid page is an error. Paper-ready collection requires every
selected GraphQL thread and its nested comments to be complete; a missing
thread, paginated child list, GraphQL/REST mismatch, or non-boolean
resolution/outdated state fails the release gate.

### 3. Export curation evidence and record decisions

Use a local clone containing every draft base, review-head, and final-head SHA:

```bash
magento2-benchmark curation-packet \
  --draft data/corpus-draft.json \
  --repository-path /path/to/magento2 \
  --thread-evidence work/thread-evidence.json \
  --output work/curation-packet.json

cp data/curation-decisions.template.json work/curation-decisions.json
```

The packet shows checkpoint source, final source, the checkpoint-to-final path
diff, the source review comment, its H `startLine`/`line` range, and available
thread evidence. Every reviewed
path has an exact `pathTransition` with `modified`, `renamed`, or `deleted`
status, its H blob, its F blob when one exists, the final path, the 50%-policy
rename score, and the path-diff digest. A rename follows the destination path
and displays its F source. A deletion has a null final path/blob and an explicit
unavailable final-source record; it is never represented as an unexplained
blank file. Unsupported or ambiguous Git transitions fail packet generation.
The packet does not create labels. Curators must explicitly decide
actionability, issue presence at the checkpoint, review acceptance,
same-root-cause remediation, thread disposition, atomicity, category, severity,
and fix evidence. Null values and `include: false` are not implicit passes.

The paper-ready release gate requires the packet's ordered case set and ordered
comment set to match the draft exactly, including B/H/F identities, size band,
file count, source URL, reviewer metadata, comment body, both H range
coordinates, and diff hunk.
It also requires the retained checkpoint-to-final path diff to be non-empty,
recomputes its SHA-256 instead of trusting the packet's declared digest, and
checks the status-dependent path/blob/source shape. This packet check detects
internal inconsistency; the later materializer independently regenerates the
transition and diff from H and F Git objects.

Every included paper-ready issue needs:

- one atomic, actionable defect or harmful practice present at the frozen
  review head;
- complete authenticated GraphQL thread evidence and a `fixed` disposition;
- a fix commit descended from the review head that changes the affected path;
- `code_change` evidence whose `artifactDigest` equals the curation packet's
  checkpoint-to-final path-diff digest;
- `thread` or `review_thread` evidence whose `artifactDigest` equals the
  canonical digest of the collected thread;
- two distinct annotators, each with an `accept` record whose `recordDigest`
  covers that annotator, verdict, timestamp, case/comment/body identity,
  released-decision digest, REST archive, GraphQL thread, and curation-packet
  digests; and
- a final accepted adjudication whose annotator set exactly matches those
  independently digest-bound records.

Paper-ready exclusions need an explicit `exclusionReason` plus an
`adjudication` with `status: "excluded"`, two distinct annotators, and one
digest-bound `verdict: "exclude"` record per annotator. An exclusion must also
name the same comment/body in the exact curation packet and have a complete
GraphQL thread whose REST message IDs reconcile, resolution/outdated states are
available booleans, and root ID/body/update time match the archived comment.
Each exclusion record binds both the canonical thread digest and packet digest.
Incomplete or REST-only thread evidence, a missing packet comment, or any
binding mismatch fails closed. Every one of the 50 cases must retain at least
one accepted comment.

### 4. Pass the release gate and materialize the corpus

Create a selection only after all 121 decision entries have been resolved:

```bash
magento2-benchmark release-selection \
  --draft data/corpus-draft.json \
  --decisions work/curation-decisions.json \
  --source-archive work/draft-source-archive.json \
  --thread-evidence work/thread-evidence.json \
  --curation-packet work/curation-packet.json \
  --paper-ready \
  --output work/released-selection.json

magento2-benchmark --config config.toml materialize \
  --selection work/released-selection.json \
  --repository-path /path/to/magento2 \
  --required-cases 50 \
  --output work/corpus.json

magento2-benchmark validate \
  --corpus work/corpus.json \
  --required-cases 50 \
  --paper-ready
```

`release-selection --paper-ready` requires the REST source archive, thread
evidence, and curation packet. It verifies their digests, exact PR/comment/review
coverage, the complete raw GraphQL request/response pagination chain,
normalized-thread-to-raw-node/page identity, source-root identity, code/thread
artifact bindings, and independent annotator records; filenames alone provide
no trust. For every included root, release also embeds the exact raw REST root,
all of its replies, and every referenced submitted-review response, bound to
the source archive and case-evidence digests. It requires the raw GraphQL and
REST message sets to be identical and compares URL, author/type, body,
timestamps, reply identity, submitted-review identity/state/commit, and the
REST-enriched normalized fields.
`materialize` refetches PR/review facts, recomputes rename-aware paths and diff
digests from Git, and requires every selected comment's observed H→F status,
source/final paths, H/F blob OIDs, rename score, and diff digest to equal the
packet evidence retained in the released selection. A re-sealed fabricated or
stale packet therefore fails before corpus output even when its internal hashes
are self-consistent. Materialization also verifies B→H→F ancestry, merge
parentage, B as the reviewed mainline merge base, and reachability of the merge
from a frozen mainline cutoff, then retains a digest-bound proof record. It
cross-reconciles every normalized PR/comment/review field with its full archived
REST object, verifies the retained archive/case/object digest chain, and
produces the immutable released-corpus format. Released
gold retains its annotator list, thread-completeness flag, and fixed thread
disposition, so `validate --paper-ready` rechecks those facts as the final
offline semantic gate. Passing ordinary `validate` only proves the weaker
structural/provisional contract.

The corpus retains each case's raw GraphQL page archive and each gold
comment's normalized-thread binding plus its exact raw REST thread projection.
`validate --paper-ready` independently recomputes the
request/response/page/archive digests, cursor coverage, raw node digest, REST
evidence digest and archive/case bindings, three-way GraphQL/REST/normalized
message reconciliation, source-root/review equality, and adjudication
bindings. Materialization additionally compares the embedded REST projection
with the current cache and rejects an added, removed, or changed reply. A
normalized thread with only a self-digest, a missing page, or re-sealed
GraphQL/normalized content that conflicts with REST is rejected.

Collection, curation, materialization, running, and judging all construct Git
diffs with a fixed policy independent of local user configuration:
Myers algorithm, indent heuristic disabled, external diff and text-conversion
drivers disabled, text forced, rename detection fixed at 50% with a fixed
limit, and replace refs ignored. Color, quoting, source/destination prefixes,
context/inter-hunk context, submodule handling, and output indicators are
pinned explicitly. System/global attribute files are disabled; nonempty
host-only `.git/info/attributes`, legacy grafts, shallow history, and
repository-local `diff.*` configuration fail closed. Evidence reads also set
`GIT_NO_REPLACE_OBJECTS=1`; offline reads additionally disable lazy fetching.
Their subprocess environment drops inherited `GIT_*` redirection and
config-injection variables before applying those controls, while retaining
non-Git environment such as proxy variables for live fetches. The released
corpus records this exact policy; a diff or path digest produced under other
settings is rejected.
Eligible inline anchors may target either an added RIGHT-side line or an
unchanged RIGHT-side context line shown in the frozen diff. The corpus records
`lineKind` and a distinct context-anchor validity method; LEFT/deleted or
out-of-diff lines remain ineligible.

GitHub's coordinate names are intentionally not collapsed. The historical H
anchor is `original_commit_id` plus `original_line` and nullable
`original_start_line`; `line` and `start_line` are the current PR-head
projection and may move or become null after a later commit. The provisional
draft calls its normalized H fields `line`/`start_line` but also retains exact
`raw_current_*` and `raw_original_*` values plus `raw_start_side`.
`archive-draft-sources` compares those fields to their corresponding REST
properties, never draft `start_line` to REST current `start_line`.
Authenticated GraphQL evidence separately retains and cross-reconciles
`line`, `originalLine`, `startLine`, `originalStartLine`, `diffSide`, and
`startDiffSide` against the exact REST root and normalized thread. Missing,
swapped, or self-consistently re-sealed coordinates fail paper-ready release.

The released format is described by
[`released-corpus.schema.json`](schemas/released-corpus.schema.json). Runtime
validation remains authoritative because several cross-record and paper-ready
invariants cannot be expressed completely in JSON Schema.

### 5. Plan and apply the fork replay

Before handing execution to a blinded operator, a label custodian projects the
strict paper-ready release into the standalone analysis execution corpus:

```bash
magento2-benchmark execution-corpus \
  --corpus work/corpus.json \
  --output work/analysis-execution-corpus.json
```

This is the last pre-unseal command allowed to read the released labeled
corpus. The projection contains only the corpus/repository identity and the
ordered `caseId`, public partition/size band, B/H SHAs, deterministic diff and
changed-path identities, and replay refs. Its exact schema is
[`analysis-execution-corpus.schema.json`](schemas/analysis-execution-corpus.schema.json).
Generation requires the full strict 50-case paper-ready release, recursively
rejects label-shaped keys and any known non-public reviewer/comment,
expected-issue, adjudication, decision, or fix value, and seals the result with
`executionCorpusDigest`.

Planning is read-only and accepts only that label-free artifact:

```bash
magento2-benchmark replay-plan \
  --execution-corpus work/analysis-execution-corpus.json \
  --fork YOUR_ORG/magento2-benchmark \
  --output work/replay-plan.json
```

Inspect and approve the plan. `replay-apply` is the **only external mutation
boundary** in this module:

```bash
magento2-benchmark --config config.toml replay-apply \
  --plan work/replay-plan.json \
  --confirm-fork YOUR_ORG/magento2-benchmark \
  --source-repository /path/to/magento2 \
  --git-remote benchmark-fork \
  --output work/replay-lock.json
```

It creates immutable base/head refs and one PR per case. Existing exact refs
and PRs are reused. A ref at the wrong SHA is a hard failure and is never
force-updated. Replay PR titles and bodies are opaque and omit reviewer
comments, expected issues, source PR URLs, and reviewer identities.

Immediately before a frozen run, live-check every fork ref and PR and seal the
observations:

```bash
magento2-benchmark --config config.toml verify-replay \
  --execution-corpus work/analysis-execution-corpus.json \
  --replay-lock work/replay-lock.json \
  --output work/replay-attestation.json
```

This read-only command requires `GITHUB_TOKEN`, verifies the fork/upstream
identity and all 50 base refs, head refs, PR numbers, URLs, repositories, and
SHAs, and binds them to the corpus digest, execution-corpus digest, embedded
replay plan, and lock. The runner copies the execution corpus, lock, and
attestation into each run directory. A frozen run
also requires `collectedAt` to precede its invocation and to be no older than
`analysis.replay_attestation_max_age_seconds` (at most 3,600 seconds for the
paper gate). Diagnostic runs may omit the attestation; paper runs fail before
analysis when it is absent, stale, or future-dated. The receipt is a
digest-bound capture of an authenticated live check, not a GitHub-signed
statement; publish it and independently rerun `verify-replay` when auditing a
paper package.

The configured CodeCrow project must point at this exact replay fork:
`analysis.project_vcs_workspace` must equal the lock's fork owner and
`analysis.project_vcs_repo_slug` must equal its repository name, including
case. For the example above those values are `YOUR_ORG` and
`magento2-benchmark`, not the upstream `magento/magento2` coordinates. The
runner checks this before runtime preflight or submission of any case.

### 6. Index every frozen base outside the runner

An operator must index each of the 50 unique replay base refs at its exact base
SHA in the benchmark workspace/project before analysis. This is deliberately
outside the benchmark runner: **`run` never creates, updates, or repairs an
index**.

Use the existing CodeCrow repository indexing operation for every
`cases[].baseRef`/`cases[].baseSha` pair in `replay-lock.json`. Every request
must set:

```json
{
  "workspace": "Magento Benchmark",
  "project": "magento2-core-review",
  "branch": "benchmark/magento2/m2b-001/base",
  "commit": "<exact base SHA>",
  "preserve_other_branches": true
}
```

`preserve_other_branches: true` is mandatory. The RAG indexing default is
false, which would discard the previously indexed benchmark branches while
loading the next one. The repository path and branch checkout are
deployment-specific operator inputs.

After all branches are loaded, preflight every receipt:

```text
GET /index/{workspace}/{project}/revision?branch={exact-base-ref}&commit={exact-base-sha}
```

Each exact-revision receipt must identify the requested `workspace`, `project`,
`branch`, and full `commit`, with `repository_revision` equal to that commit.
It must describe one sealed repository-index generation with these exact
fields:

- `generation_schema = "codecrow.repository-index-generation"`;
- a positive `generation_member_count` with
  `point_count = generation_member_count + 1`;
- valid `generation_members_sha256`, `generation_manifest_sha256`, and
  acquisition-bound `source_tree_sha256`;
- a valid `repository_facts_sha256`;
- unique `plugin_ids` containing `php` and `magento`; and
- valid `plugin_fingerprint`, `plugin_descriptor_fingerprint`,
  `plugin_implementation_fingerprint`, and
  `index_representation_fingerprint` values.

Save all receipts only after the final branch has been indexed. The runner
repeats this check before any job is queued and after the run, and fails if a
receipt is incomplete, refers to another exact revision or generation, or
changes during analysis.

Run the standalone operator preflight before preregistration execution or any
analysis command:

```bash
PYTHONPATH=src python -m magento2_benchmark.preflight \
  --config config.toml \
  --execution-corpus work/analysis-execution-corpus.json \
  --replay-lock work/replay-lock.json \
  --replay-attestation work/replay-attestation.json \
  --repository-path /path/to/magento2 \
  --output work/operator-preflight.json
```

This command is strictly read-only except for its requested output artifact. It
does not create or repair an index, enqueue analysis, call either model, invoke
the product finalizer, or mutate GitHub. It validates the label-free
50-case execution corpus, primary replay lock, optional supplied attestation and its
paper freshness, fork/project/RAG identity, configured model and exact expected
response IDs, secret environment-variable **presence** (never values), and the
required Redis/model-call/retrieval/runtime evidence controls. It also inspects
the three configured containers and proves every local B/H object, ancestry,
diff, changed-path set, and canonical B source-tree identity.

The preflight performs exactly one read-only exact-revision receipt lookup for
each locked B ref/SHA. In addition to the per-receipt validation above, it
requires all 50 receipts to share the same plugin, descriptor,
implementation, neutral representation, and include/exclude selection-policy
controls. Every acquisition `source_tree_sha256` must equal a checkout-free
reconstruction from the local immutable B Git tree. The output stores only
safe projections and digests of the complete receipts.

Every prerequisite has a `passed`, `failed`, or `blocked` check with explicit
failure codes. The entire JSON object is bound by `readinessDigest` and follows
[`operator-preflight.schema.json`](schemas/operator-preflight.schema.json).
`runReady: true` means all **pre-run** prerequisites checked by this command
were proven at `generatedAt`. `paperReady` is always false: registration/seal
completion, H/F analysis, judgments, blinded human audits, metrics, and the
publication package do not exist yet, and the explicit
`analysis_and_post_run_protocol_evidence_not_yet_produced` blocker records that
boundary. Omitting `--replay-attestation` is allowed for diagnostics but makes
`runReady` false. The process exits with status 2 when any run prerequisite
fails.

### 7. Preregister the publication study

Before any analysis run, create a study plan that names every exact
`analysisPlans[].runId`, `judgePlans[].judgmentId`, model/provider/config and
prompt digest, the sealed and secondary endpoints, the fixed comparison
controls, at least 10,000 paired PR-cluster bootstrap iterations, stopping and
failure rules, the post-fix control, the human-audit sample, and allowed and
prohibited claims:

```bash
magento2-benchmark register-study \
  --corpus work/corpus.json \
  --plan work/study-plan.json \
  --output work/study-registration.json
```

The registration binds the strict 50-case corpus and its deterministic 30/20
partition. Its schemas are
[`study-registration.schema.json`](schemas/study-registration.schema.json),
[`seal-ledger.schema.json`](schemas/seal-ledger.schema.json), and
[`judge-evaluation.schema.json`](schemas/judge-evaluation.schema.json).
Runtime validation is authoritative for cross-artifact digests and time
ordering.

The released corpus JSON contains the sealed labels. The commitment and ledger
are therefore procedural, digest-bound custody evidence—not encryption,
zero-knowledge concealment, or trusted timestamping. Custodians and storage
controls must prevent access until all preregistered analysis runs complete.
The ledger requires two custodians, exactly one commitment and unseal, and a
post-unseal sealed-access record, and proves only that the supplied records are
internally consistent.

### 8. Run one or more analysis models

Set the benchmark project, fork, Redis/RAG endpoints, Java finalizer endpoint,
and model in `config.toml`. The production transport is the CodeCrow Redis job
queue. `transport = "http"` targets the legacy `/review` compatibility path,
does not expose terminal retrieval events, and therefore cannot satisfy the
paper-run retrieval gate. Use HTTP only for explicitly labelled smoke tests.

```bash
magento2-benchmark --config config.toml run \
  --execution-corpus work/analysis-execution-corpus.json \
  --replay-lock work/replay-lock.json \
  --replay-attestation work/replay-attestation.json \
  --repository-path /path/to/magento2 \
  --run-id study-2026:model-a \
  --analysis-model provider/model-a \
  --output-dir runs/analysis-model-a

magento2-benchmark --config config.toml run \
  --execution-corpus work/analysis-execution-corpus.json \
  --replay-lock work/replay-lock.json \
  --replay-attestation work/replay-attestation.json \
  --repository-path /path/to/magento2 \
  --run-id study-2026:model-b \
  --analysis-model provider/model-b \
  --output-dir runs/analysis-model-b
```

`--analysis-model` overrides `analysis.model`.
`--run-id` accepts the preregistered 1–256 character safe identifier used by
`analysisPlans[].runId`; exploratory runs generate an `m2b-...` identifier
when it is omitted. Supplying it on resume requires an exact match with the
existing manifest.
`--expected-analysis-response-model` pins the exact model identifier that the
provider must report; when an analysis-model override is supplied without an
explicit expectation, that override is used for both roles. `--case` is
repeatable for a development subset and `--limit` supports smoke runs. The
primary and verified-F pre-unseal runners reject the released corpus kind;
there is no labeled-corpus fallback. Every analysis request
has an empty prior-issues list, uses the exact replay base/head, and includes no
reviewer evidence. Every requested `--case` must exist in the corpus; mixing a
valid case with a misspelled ID is rejected rather than silently narrowing the
run. A new run accepts only a missing or empty output directory. If the
directory already contains any file, use `--resume` with its `run.json` or
choose a new directory.

`--resume` is deliberately strict. It reuses a manifest only when its digest,
corpus/execution-corpus/model/provider, transport, finding semantics, redacted analysis-config
digest, replay-lock digest, runtime provenance, and exact ordered selected-case
IDs all match. It also revalidates every completed case's exact index receipt
and every attempt artifact/digest before skipping it.
The same complete validation runs in the paper metrics gate: it reopens every
historical start/result artifact, including earlier failed and recovered
interrupted attempts, verifies exact artifact fields and identity, request and
result digests, result projection, status, duration, error, retry eligibility,
termination/stopping reason, contiguous attempt numbers, cap, and case-summary
binding. Omitting an earlier entry, deleting its artifact, or resealing it with
an impossible retry/stopping policy makes the run ineligible.

Analysis retries are explicit and bounded. `analysis.max_case_attempts`
defaults to `1` and is included in the redacted configuration digest. One
attempt per unfinished case is made in each invocation; only `--resume` may
append the next attempt, and the original cap cannot change. Each attempt gets
a unique immutable start artifact and result artifact under `raw/attempts/`.
`run.json` retains the append-only attempt ledger, artifact digests, fresh job
ID, error, retry eligibility, and one of `case_completed`,
`explicit_resume_required`, or `attempt_limit_reached`. An invocation that
ended while an attempt was running is closed as a preserved interrupted
attempt before any retry is appended. Once the cap is reached, resume records
and returns the same partial run without submitting another request. A later
successful attempt updates the case summary but never removes or overwrites an
earlier failure.

Each attempt's redacted request contains the exact analysis request with
credentials and secret-like custom fields replaced by `<redacted>`; its digest
binds the remaining submitted model/provider, B/H refs, fork PR identity,
diff, changed files, enrichment, empty prior issues, and RAG flag. A second
request-control digest replaces only `aiModel`, so otherwise different
requests cannot masquerade as a controlled model comparison. Secret-like
values below `aiCustomParameters` make a run diagnostic because their
redaction prevents exact reconstruction. Any manifest, ledger, or artifact
drift aborts resume. If an analysis, event-stream, or finalizer response echoes
the API key, internal service secret, or a secret-like custom request value,
the case fails before that response is persisted; any resulting manifest error
is credential-redacted.

Paper runs set `analysis.require_model_call_evidence = true`, use Redis
transport, and enable the inference worker with
`REVIEW_QUALITY_CAPTURE_ENABLED=true` plus a
`REVIEW_QUALITY_CAPTURE_PROJECT_IDS` allowlist containing the benchmark
project ID. `analysis.quality_capture_container_dir` must equal the worker's
`REVIEW_QUALITY_CAPTURE_OUTPUT_DIR` (the shared default is
`/app/logs/review-quality-captures`). The runner accepts exactly one terminal
`review_quality_capture_completed` receipt per successful attempt, requires
every completed provider call to report the configured
`analysis.expected_response_model`, and copies the referenced capture from the
configured analysis container into that attempt's immutable artifact set.

The copy is accepted only when its container path is a direct, regular,
non-symlink child of the configured capture directory and its filename binds
the project and replay PR. The capture and receipt digests, requested model,
provider-reported models and per-stage calls are revalidated. Its full
credential-redacted Pydantic request snapshot must exactly equal the submitted
benchmark request after the documented `branch` alias, fixed DTO defaults,
`[REDACTED]` secret replacement, and base-URL userinfo/query/fragment removal.
That equality includes custom model parameters, project capabilities, complete
enrichment, diff, rules, and task context. Missing or duplicate receipts,
unsafe paths/files, a model mismatch, or any request/artifact drift fails the
attempt. A later explicit resume preserves that failure and may append a new
attempt; it never replaces the evidence. The final run and metrics
configuration expose requested, expected, aggregate provider-reported, and
per-stage provider-reported model roles.

The runner does not score the inference worker's raw `issues` array. It sends
that captured result and the frozen head-revision file contents to the
secret-protected web-server endpoint
`/api/internal/analysis/benchmark-finalize`. That endpoint applies the same
`AiAnalysisClient` result validation and `CodeAnalysisService` issue mapping,
snippet anchoring, filtering, and ingestion de-duplication used by product PR
analysis. It returns transient first-iteration issues without reading prior
issue state, persisting an analysis or snapshot, or publishing to the fork.
The redacted request, raw result, retrieval events, and Java-finalized result
are retained together under `raw/`; only the Java-finalized issues are judged.

With `analysis.require_retrieval_evidence = true` (required for paper runs), a
case completes only after exactly one terminal `review_evidence_completed`
status event proves that every registered review unit completed, every
deterministic retrieval state is `complete`, at least one review unit was
processed, semantic retrieval was enabled, semantic failures are zero, and the
exact evidence-ID count is well formed. The same event must state that PR
indexing completed and bind the fork PR number, target ref, exact H/B
revisions, sealed base-generation manifest, and canonical PR-overlay
fingerprint plus its complete membership-manifest digest. The event also binds
the base plugin selection, descriptor, implementation, and neutral index
representation fingerprints to the preflight receipt. The raw Redis stream
must contain exactly one matching final event as its last event, contain no
error event, bind the case job ID, and carry the same result later finalized by
Java. The runner hashes the terminal event into `retrievalEvidence`. Missing,
duplicate, malformed, disabled, unbound, or incomplete evidence fails the case;
it is never inferred from the preflight receipt.

For a paper run set `analysis.require_runtime_provenance = true`. The runner
then requires immutable container and image IDs for the configured
`codecrow-inference-orchestrator` analysis engine, RAG pipeline, and
`codecrow-web-application` product finalizer containers. Preserve `run.json`
and its `raw/` directory.

### 8a. Execute the separate verified-F control lane

The preregistered post-fix control replays each case at the source PR's exact
`finalHeadSha` (F). It is a separate B→F lane; it never replaces the primary
B→H replay or changes the primary precision, recall, or F1 denominator.

Create and inspect the deterministic F plan, then create separate immutable
refs and pull requests in the same confirmed fork:

```bash
magento2-benchmark post-fix-replay-plan \
  --corpus work/corpus.json \
  --primary-replay-lock work/replay-lock.json \
  --repository-path /path/to/magento2 \
  --output work/post-fix-plan.json

magento2-benchmark post-fix-replay-apply \
  --execution-corpus work/analysis-execution-corpus.json \
  --primary-replay-lock work/replay-lock.json \
  --plan work/post-fix-plan.json \
  --confirm-fork YOUR_ORG/magento2-benchmark \
  --source-repository /path/to/magento2 \
  --git-remote benchmark-fork \
  --output work/post-fix-lock.json

magento2-benchmark verify-post-fix-replay \
  --execution-corpus work/analysis-execution-corpus.json \
  --primary-replay-lock work/replay-lock.json \
  --post-fix-replay-lock work/post-fix-lock.json \
  --output work/post-fix-attestation.json
```

Only the label custodian's `post-fix-replay-plan` command reads the released
corpus to select each exact `sourcePr.finalHeadSha`. Its output is recursively
label-free and fails closed unless B < H < F is strict and every B→F diff and
path set can be regenerated from local Git objects. The plan, lock, and live
attestation all bind the same canonical `executionCorpusDigest`; apply,
verification, and F analysis accept only that label-free execution corpus.
Fix-evidence and H→F path-transition bindings are derived and checked after
unseal by the F judge. The F pull-request title/body contains fixture identity
only; reviewer evidence is not sent to CodeCrow.

Run F before unsealing labels:

```bash
magento2-benchmark --config config.toml post-fix-run \
  --execution-corpus work/analysis-execution-corpus.json \
  --registration work/study-registration.json \
  --primary-replay-lock work/replay-lock.json \
  --post-fix-replay-lock work/post-fix-lock.json \
  --post-fix-replay-attestation work/post-fix-attestation.json \
  --primary-run runs/analysis-model-a/run.json \
  --repository-path /path/to/magento2 \
  --output-dir runs/post-fix-model-a
```

This command reuses the primary H run's analysis model, public configuration,
exact B index-generation receipts, case order, retry policy, and immutable
analysis/RAG/finalizer image identities. It uses the existing B index plus the
normal F pull-request overlay; it never indexes. Configuration, B receipt,
runtime-image, replay, case, or artifact drift is a hard failure. The result is
a distinct `codecrow-magento2-post-fix-analysis-run`, so primary H metrics
cannot consume it accidentally.

### 9. Unseal, judge, and run the blinded human audit

After every preregistered H and verified-F 50-case analysis run has status
`completed`, record custody and unseal evidence. The validator enforces
`registration < run start <= run completion <= unseal <= ledger generation`;
any sealed-access event before unseal fails:

```bash
magento2-benchmark seal-study \
  --corpus work/corpus.json \
  --registration work/study-registration.json \
  --analysis-run runs/analysis-model-a/run.json \
  --analysis-run runs/analysis-model-b/run.json \
  --post-fix-analysis-run runs/post-fix-model-a/run.json \
  --post-fix-analysis-run runs/post-fix-model-b/run.json \
  --plan work/seal-ledger-plan.json \
  --output work/seal-ledger.json
```

The judge uses an OpenAI-compatible chat-completions endpoint. Configure a
fixed odd repeat count and override the model per run if needed:

```bash
magento2-benchmark --config config.toml judge \
  --corpus work/corpus.json \
  --run runs/analysis-model-a/run.json \
  --repository-path /path/to/magento2 \
  --judgment-id study-2026:judge-model-a \
  --judge-model provider/judge-model \
  --expected-response-model provider/resolved-model-id \
  --output-dir runs/judge-model-a
```

`--judgment-id` supplies the exact preregistered safe ID; exploratory judging
generates an `m2j-...` ID when it is omitted. `--judge-model` overrides
`judge.model`. If it is supplied without
`--expected-response-model`, the requested alias is also the required response
model. Use the latter flag only when a routing provider returns a different,
predeclared identifier. A missing or different provider model fails the live
call and any resumed checkpoint.

The judge evaluates every
gold/candidate pair, then performs maximum-cardinality, maximum-evidence
one-to-one assignment. One CodeCrow finding cannot satisfy multiple reviewer
issues, and one reviewer issue cannot credit multiple findings. Unmatched
findings may receive a secondary novel-finding verdict, but that verdict does
not silently expand the gold set.

Set `judge.expected_response_model` to the exact model identifier that the
provider returns. It may equal the requested model or be a preregistered
provider-resolved identifier, but an internally consistent response from any
other model is rejected.

Gold-pair prompts retain every candidate. If the unabridged prompt exceeds
`judge.max_prompt_characters`, the judge deterministically finds one uniform
character cap for every candidate title, description, suggested fix, frozen
path diff, and source window. It records the compaction policy, cap, configured
maximum, and preserved candidate count in the prompt. If the minimum complete
prompt still exceeds the limit, judging fails instead of dropping candidates.

Transport retries (`judge.max_retries`) are separate from structured-output
retries (`judge.max_structured_retries`). A syntactically valid provider reply
that violates the expected rubric is retained as a rejected attempt and retried
until one response validates or the configured limit fails the run.

Judging resumes automatically when the same output directory is reused. Each
validated repeat is atomically saved under `checkpoints/<case>/`, bound to the
case input, judge config, system prompt, and user prompt. A completed case is
then atomically saved under `raw/` with its own input/config/content digests.
On restart, valid call and case checkpoints are reused; stale, modified, or
differently bound checkpoints are hard failures.

Preserve `judgments.json`, `checkpoints/`, and `raw/`. For every successful
repeat the client stores the exact system prompt, exact user prompt, parsed
JSON response, rejected structured attempts, model/usage metadata, the exact
credential-free provider request, the complete parsed provider response
envelope, and all binding hashes. The paper gate reopens those artifacts,
reconstructs repeat majorities and assignments, and rejects a summary that
cannot be reproduced from the raw calls. Provider envelopes are auditable
captures, not cryptographic provider signatures. Secret-like custom request
fields are recursively redacted from disk; because that prevents exact request
reconstruction, a redacted judge request is diagnostic rather than paper-ready.
If the provider response echoes any literal API key or secret-like custom
request value, the call fails before its response is checkpointed.
The format is described by
[`judgment.schema.json`](schemas/judgment.schema.json).

Judge the F lane only after unseal, using its registered deterministic ID and
the same judge model, public configuration, response-model expectation, repeat
count, and evidence rubric as the paired H judgment:

```bash
magento2-benchmark --config config.toml post-fix-judge \
  --corpus work/corpus.json \
  --registration work/study-registration.json \
  --seal-ledger work/seal-ledger.json \
  --primary-run runs/analysis-model-a/run.json \
  --primary-judgment runs/judge-model-a/judgments.json \
  --post-fix-run runs/post-fix-model-a/run.json \
  --post-fix-replay-lock work/post-fix-lock.json \
  --repository-path /path/to/magento2 \
  --output-dir runs/post-fix-judge-model-a

magento2-benchmark post-fix-control \
  --corpus work/corpus.json \
  --registration work/study-registration.json \
  --seal-ledger work/seal-ledger.json \
  --primary-replay-lock work/replay-lock.json \
  --primary-run runs/analysis-model-a/run.json \
  --primary-judgment runs/judge-model-a/judgments.json \
  --post-fix-run runs/post-fix-model-a/run.json \
  --post-fix-replay-lock work/post-fix-lock.json \
  --post-fix-replay-attestation work/post-fix-attestation.json \
  --post-fix-judgment \
    runs/post-fix-judge-model-a/post-fix-judgment.json \
  --output work/post-fix-control-model-a.json
```

For each gold issue matched at H, the verified-F result is `disappeared`,
`still_detected`, or `unverifiable`. H-unmatched issues are
`not_applicable_primary_unmatched` and never enter this control's denominator.
The reported disappearance rate is conditional on primary H true positives;
it is not recall, a precision estimate, or proof that CodeCrow caused the
source fix. An H true positive with no F findings is an observable
`disappeared`; an incomplete or ambiguous same-root comparison remains
`unverifiable`.

Build the exact registered control set from a path-only manifest whose
`controls[]` entries name the control, seal ledger, primary replay lock,
primary run/judgment, and post-fix run/lock/attestation/judgment:

```bash
magento2-benchmark post-fix-control-set \
  --manifest work/post-fix-control-set-input.json \
  --output work/post-fix-control-set.json
```

The control-set validator opens and semantically validates every raw artifact;
a list of IDs and hashes is insufficient.

Export the opaque human-audit subjects after unseal. The packet includes the
gold/candidate evidence, decision-kind-specific allowed human verdicts, and a
concise rubric, but omits model, judgment, and machine-verdict identity:

```bash
magento2-benchmark judge-evaluation-packet \
  --corpus work/corpus.json \
  --registration work/study-registration.json \
  --seal-ledger work/seal-ledger.json \
  --analysis-run runs/analysis-model-a/run.json \
  --analysis-run runs/analysis-model-b/run.json \
  --post-fix-analysis-run runs/post-fix-model-a/run.json \
  --post-fix-analysis-run runs/post-fix-model-b/run.json \
  --judgment runs/judge-model-a/judgments.json \
  --judgment runs/judge-model-b/judgments.json \
  --output work/blinded-audit-packet.json
```

Collect at least the preregistered number of independent, model-identity-blind
human verdicts for every packet `subjectId`; independently adjudicate every
disagreement. Then create the evaluation artifact:

```bash
magento2-benchmark judge-evaluation \
  --corpus work/corpus.json \
  --registration work/study-registration.json \
  --seal-ledger work/seal-ledger.json \
  --analysis-run runs/analysis-model-a/run.json \
  --analysis-run runs/analysis-model-b/run.json \
  --post-fix-analysis-run runs/post-fix-model-a/run.json \
  --post-fix-analysis-run runs/post-fix-model-b/run.json \
  --judgment runs/judge-model-a/judgments.json \
  --judgment runs/judge-model-b/judgments.json \
  --plan work/judge-evaluation-plan.json \
  --output work/judge-evaluation.json
```

Validation reconstructs every gold and candidate from the bound corpus/run,
requires every corpus case, the complete gold×candidate pair edge set and
exact unmatched/novel decisions, and derives the exact audit universe. It
rejects missing, extra, or arbitrary subjects. It reports human–human
agreement and final judge–human agreement/confusion. Blinded-audit records and
adjudications must follow the ledger's unseal and sealed-access event and their
bound completed judgments.

The current audit artifact covers primary pair and novel-finding decisions,
but not derived per-gold verified-F outcomes. Until those `post_fix_gold`
subjects receive the same two-rater, disagreement-adjudication, and exact
control binding, metrics and package verification preserve the explicit
`post_fix_judge_human_audit_not_bound` publication blocker. Machine F controls
may execute and remain useful diagnostics; they cannot make
`publicationProtocolReady` or `paperReady` true.

### 10. Compute metrics, dashboard, and rerun package

Pass one judgment artifact per analysis/judge configuration:

```bash
magento2-benchmark metrics \
  --corpus work/corpus.json \
  --judgment runs/judge-model-a/judgments.json \
  --judgment runs/judge-model-b/judgments.json \
  --analysis-run runs/analysis-model-a/run.json \
  --analysis-run runs/analysis-model-b/run.json \
  --post-fix-analysis-run runs/post-fix-model-a/run.json \
  --post-fix-analysis-run runs/post-fix-model-b/run.json \
  --post-fix-control-set work/post-fix-control-set.json \
  --post-fix-artifact work/post-fix-plan.json \
  --post-fix-artifact work/post-fix-lock.json \
  --post-fix-artifact work/post-fix-attestation.json \
  --post-fix-artifact runs/post-fix-judge-model-a \
  --post-fix-artifact work/post-fix-control-model-a.json \
  --repository-path /path/to/magento2 \
  --study-registration work/study-registration.json \
  --seal-ledger work/seal-ledger.json \
  --judge-evaluation work/judge-evaluation.json \
  --bootstrap-iterations 10000 \
  --seed 20260729 \
  --output reports/metrics.json

magento2-benchmark dashboard \
  --metrics reports/metrics.json \
  --output-dir reports/dashboard

python -m http.server 8080 --directory reports/dashboard
```

Open `http://127.0.0.1:8080`. The dashboard copies the exact metrics bytes to
`data.json`; it does not recalculate scores. It shows model/judge provenance,
sealed confirmatory coverage and precision/recall/F1, secondary all-50 and
development aggregates, size-band results, paired sealed deltas, uncertainty
exclusions, and per-case gold/candidate assignments.

`--analysis-run` is optional only for diagnostic metrics. For
artifact-integrity evaluation, supply the exact run for every judgment. Run IDs must match
the judgment set exactly, and metrics revalidate corpus/run digests, analysis
model identity, completed scored cases, and every normalized candidate finding.
It uses `--repository-path` to regenerate every redacted analysis request and
judge code-evidence prompt from the frozen B/H Git objects; a missing clone or
self-consistent source/enrichment substitution keeps metrics diagnostic.
It also reopens each run's copied replay lock and live attestation and validates
their complete corpus/plan/ref/PR binding.
Only then can `analysisArtifactsBound` and the per-configuration
`analysisArtifactBound` fields be true. `methodology.artifactIntegrityReady`
also requires the strict corpus, full resolved judgment/run coverage, at least
10,000 bootstrap iterations, completed Redis runs,
immutable analysis/RAG/finalizer identities, product-finalized findings,
an exact digest-bound redacted request for every case, terminal retrieval
evidence, sealed index generations, stable before/after receipts, a positive
preregistered bootstrap-iteration count, raw judgment
cases, and exact provider request/response checkpoints from which every repeat,
majority verdict, and assignment can be reconstructed. Any unmet machine
condition is named in `artifactIntegrityGateFailures`.

The registration, seal, and judge-evaluation inputs are all-or-none. When they
validate, metrics reports
their individual `protocolControls` statuses and removes those three missing
controls from the protocol failure list. This machine gate is deliberately not
a publication claim. A post-fix control-set digest alone is never trusted.
Pass every registered F run with `--post-fix-analysis-run`, the exact control
set with `--post-fix-control-set`, and repeat `--post-fix-artifact` for the raw
F replay plan/lock/live attestation, F judgments, controls, and control set.
Metrics invokes the semantic H/F control-set validator before removing
`post_fix_control_not_bound`.

`publicationProtocolReady` and `paperReady` remain false until the
preregistered blinded human audit also covers every F per-gold decision and
the finalized rerun package has been independently verified. The explicit
`post_fix_judge_human_audit_not_bound` blocker prevents machine-only F controls
from being presented as calibrated publication evidence. All blockers are
reported in
`publicationProtocolGateFailures` and included in `paperGateFailures`; the
dashboard never labels an artifact paper-ready.

For a multi-model paper comparison, metrics also computes one control digest
after replacing only the selected review model with a sentinel. Provider,
custom parameters, retry/failure policy, replay lock, case order, index
generations, product semantics, and immutable service images must remain
identical. Container instance IDs may differ. A comparison that changes any
other factor remains diagnostic.

The confirmatory primary aggregate uses only the 20 sealed cases. The all-50
and 30-case development aggregates are secondary. A case with any majority
gold/candidate verdict of `unverifiable` is excluded from scoring—not converted
to FN or reference-set FP—and appears in coverage uncertainty records.

The primary metrics call unmatched candidates
`referenceSetFalsePositive`. Reviewer comments are not an exhaustive defect
inventory, so that label means “not matched to this reference set,” not “proven
invalid.” Confirmed novel-finding precision is secondary, excludes
out-of-scope/unverifiable/unadjudicated findings, and has no corresponding
expanded-gold recall or F1.

The run and metrics formats are described by
[`analysis-run.schema.json`](schemas/analysis-run.schema.json),
[`post-fix-analysis-run.schema.json`](schemas/post-fix-analysis-run.schema.json),
[`post-fix-judgment.schema.json`](schemas/post-fix-judgment.schema.json),
[`post-fix-control.schema.json`](schemas/post-fix-control.schema.json),
[`post-fix-control-set.schema.json`](schemas/post-fix-control-set.schema.json),
and [`metrics.schema.json`](schemas/metrics.schema.json).

Build the non-circular rerun package only after metrics and dashboard are
final. Every input must be under one real `--artifact-root`; symlinks, path
escapes, overlapping categories, secrets, digest drift, and a dashboard whose
`data.json` differs from metrics are rejected. Public configuration strips URL
userinfo, query strings, and fragments. Package scanning also covers manifest
instructions/limitations and text artifacts, rejecting literal secret
assignments and credential-bearing URLs while permitting `$ENV_VAR`
placeholders. Five additional evidence
families are mandatory: `source`, `curation`, `replay`, `current_comment`, and
`post_fix`. They must contain the exact discovery/linkage, draft, source/thread
archives, packet/decisions/selection, live current-comment attestation, H
replay, and registered F artifacts respectively. Placeholder inventories are
not accepted.

```bash
magento2-benchmark reproducibility-package \
  --artifact-root . \
  --corpus work/corpus.json \
  --registration work/study-registration.json \
  --seal-ledger work/seal-ledger.json \
  --judge-evaluation work/judge-evaluation.json \
  --metrics reports/metrics.json \
  --dashboard reports/dashboard \
  --analysis-artifact runs/analysis-model-a \
  --analysis-artifact runs/analysis-model-b \
  --judgment-artifact runs/judge-model-a \
  --judgment-artifact runs/judge-model-b \
  --runtime-artifact work/runtime-provenance.txt \
  --config-artifact config.public.toml \
  --source-artifact work/source-evidence \
  --curation-artifact work/curation-evidence \
  --replay-artifact work/primary-replay \
  --current-comment-artifact work/current-comment-attestation.json \
  --post-fix-artifact work/post-fix-evidence \
  --rerun-instruction "Install the bound benchmark source revision." \
  --rerun-instruction "Run validation, analysis, judge, metrics, and dashboard." \
  --limitation "This corpus does not establish production causal impact." \
  --output reproducibility-package.json

magento2-benchmark verify-reproducibility-package \
  --artifact-root . \
  --manifest reproducibility-package.json
```

JSON and TOML are recursively checked for sensitive key/value pairs; common
dotenv/YAML/INI assignments and token/private-key patterns are also rejected.
Verification reruns the source-to-selection projection, replay and H/F
bindings, post-fix controls, and metrics computation. It compares every
semantic metrics field—configurations, case coverage, assignments, aggregates,
pairwise comparisons, and fixed bootstrap output—while excluding only
`generatedAt`. Rehashing a fabricated or edited metrics JSON therefore cannot
make it valid. The manifest records this result in `semanticVerification`.
The package binds finalized metrics, while metrics intentionally cannot bind
the later package manifest. Its schema is
[`reproducibility-package.schema.json`](schemas/reproducibility-package.schema.json).

## Failure semantics

- Any malformed manifest, missing SHA, digest drift, wrong corpus/plan/lock
  pairing, or local diff/path drift exits with status 2.
- Source archival fails on any pinned selected-comment drift. Paper-ready
  thread collection/release fails without authenticated, complete GraphQL
  resolution evidence; REST fallback never implies resolution.
- Replay apply requires an exact fork confirmation, never force-pushes, and
  stops on any divergent existing ref or PR.
- Verified-F planning/replay rejects non-strict B < H < F ancestry, an F SHA
  other than `sourcePr.finalHeadSha`, a fix commit outside H..F, a regenerated
  path transition/diff mismatch, a moved live ref/PR, or reuse of a primary H
  ref. F analysis rejects any paired H model/config, exact B index receipt,
  runtime-image, case-order, or replay-binding drift.
- Analysis rejects a CodeCrow project owner or repository slug that differs
  in any character from the replay lock's `forkRepository`, before submitting
  a case.
- Analysis preflights all selected exact base revisions before queueing the
  first case. Missing or inconsistent sealed-generation fields, points, SHAs,
  plugin identities/fingerprints, or changed before/after receipts fail the
  run.
- The metrics artifact-integrity gate rejects a missing or altered redacted request, a
  request whose B/H refs, model/provider, diff, changed paths, prior-issue
  state, or RAG flag disagree with the frozen case, and any unredacted API key.
- Missing/duplicate terminal retrieval evidence, incomplete review units,
  incomplete deterministic retrieval, disabled semantic retrieval, or any
  semantic retrieval failure fails that case. HTTP transport cannot satisfy
  this artifact-integrity gate.
- A missing, unauthenticated, malformed, or stateful Java product-finalizer
  response fails that case. Raw inference issues are never substituted for
  product-finalized findings.
- A per-case CodeCrow exception is recorded as `failed`; the final run status is
  `partial`. The judge records such cases as `not_scored`, and metrics expose
  coverage rather than treating them as zero-findings cases.
- A non-empty analysis output directory is never reused implicitly. Analysis
  attempts are append-only, use unique raw artifacts, require explicit resume,
  and stop at the digest-bound `analysis.max_case_attempts` cap. Failed and
  interrupted attempts remain visible even if a later attempt succeeds.
  Resume and metrics reopen every historical start/result artifact and reject
  an omitted entry, missing artifact, digest/identity drift, or invalid
  retry/stopping transition.
- A paper run fails an attempt when model-call evidence is missing, duplicated,
  outside the configured container directory, non-regular, digest-modified,
  bound to another PR/request, or reports a model other than the preregistered
  expected response model. Metrics reopen the copied capture and bind it to the
  case, terminal attempt, and raw result before accepting the run.
- Invalid judge structure is retried up to the configured structured limit and
  preserved with its validation error; exhaustion aborts judging. Digest- or
  binding-modified call/case checkpoints are never reused.
- Metrics reject foreign or digest-modified judgments/runs. A metrics artifact
  without an exact analysis run for every judgment remains diagnostic even if
  the corpus itself is paper-ready.
- Protocol metrics reject partial protocol input, preregistered IDs/configs or
  bootstrap settings that drift, an incomplete run at unseal, pre-unseal
  sealed access, an incomplete judgment edge/novel universe, arbitrary or
  missing audit subjects, unblinded raters, insufficient agreement, and audit
  timestamps outside the registered custody sequence.
- A verified-F hash inventory cannot satisfy the semantic control gate. The
  validator must reopen every F run, replay lock/attestation, judgment, raw
  case artifact, and H pairing. Even a valid machine control retains
  `post_fix_judge_human_audit_not_bound` until its per-gold outcomes are
  covered by a blinded human audit.
- Reproducibility packaging rejects a missing or unrelated corpus, paths
  outside the artifact root, symlinks, duplicate category ownership, file
  tampering, dashboard/metrics drift, credential-bearing URLs, secret-like
  JSON/TOML, manifest free text, and plain-text key/value configuration.
- The dashboard requires a local HTTP server because browsers commonly block
  `fetch("data.json")` from a `file://` page.

## Paper-run provenance checklist

Archive, without secrets:

- the repository commit containing this module and its prompt identity/digest;
- draft bytes/hash, generated draft-source archive, raw REST cache,
  authenticated GraphQL cache/thread evidence, curation packet,
  signed/identified decisions and records, released selection, corpus, and all
  declared digests;
- the review-comment edit-history limitation and the curation checks used for
  the 119 comments whose REST `updated_at` differs from `created_at`;
- primary H and verified-F replay plans/locks/live attestations and their
  separate fork ref/PR identities;
- all 50 exact sealed-generation index receipts, generation manifests/digests,
  index representation/plugin fingerprints, and evidence that
  `preserve_other_branches=true` was used;
- public analysis/judge configuration, exact provider model identifiers,
  requested/expected/provider-reported model roles, per-stage call receipts,
  custom parameters, analysis attempt cap, repeat count, timestamps, and
  immutable service image IDs;
- analysis raw responses and terminal retrieval evidence, judge call/case
  checkpoints, the complete analysis attempt ledger and every referenced
  start/result/model-call capture artifact, H and F judgments, exact bound H
  and F analysis runs, semantically validated post-fix controls/control set,
  metrics,
  bootstrap seed/iterations, dashboard bundle, failures, exclusions, and
  coverage.

Do not publish performance claims until the corpus passes both
`release-selection --paper-ready` and `validate --paper-ready`, the sealed split
has not influenced tuning, and the reporting rules in
[TECHNICAL_PAPER_PROTOCOL.md](docs/TECHNICAL_PAPER_PROTOCOL.md) are satisfied.

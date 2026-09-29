# Methodology

This benchmark measures whether CodeCrow findings recover real, actionable
issues raised during review of merged Magento 2 pull requests. Its unit is a
historical **review round**, not a final pull request.

See the [operational workflow](../README.md) for commands and
[TECHNICAL_PAPER_PROTOCOL.md](TECHNICAL_PAPER_PROTOCOL.md) for publication
controls.

## Review-round model

Each case freezes three Git revisions:

- **B — base:** the target-branch revision against which the reviewed change
  was proposed.
- **H — review head:** the exact `original_commit_id` targeted by every
  selected root reviewer comment in the case.
- **F — fix/final evidence:** a later descendant containing the adjudicated
  same-root-cause remediation. It may be an explicit fix commit or the final PR
  head, but must be recorded precisely.

The scored CodeCrow input is the `B..H` diff plus source/retrieval context at
those revisions. Scoring a final PR head would erase the issue the reviewer
identified and manufacture false negatives. `H..F` is evidence for curation,
not analysis input.

The base is either a recorded historical PR base or the merge base between H
and the merged mainline first parent. All revisions, paths, diff bytes, and
review bodies are hash-pinned. The review head must be an ancestor of F and the
merged mainline history.

Every benchmark diff is produced with Myers, the indent heuristic disabled,
external diff disabled, and rename detection fixed at 50%. These command-level
settings override local Git configuration. The corpus records the Git version
and diff policy so B..H, H..F, runner, and judge evidence use the same bytes.

## Eligibility and gold construction

A case is eligible only when:

- its PR was merged to `magento/magento2:2.4-develop`;
- the frozen `B..H` snapshot changes 3–80 paths and belongs to exactly one
  configured size band;
- selected comments are root, right-side GitHub inline comments from a human
  reviewer other than the PR author;
- all selected comments target H through `original_commit_id`, occur on a
  changed non-deleted path, and have a verifiable frozen-diff anchor on either
  an added RIGHT line or an unchanged RIGHT context line;
- each included comment expresses one actionable issue present at H;
- full thread/review evidence supports that the issue was accepted or required;
- a later same-root-cause fix is supported by code evidence and at least one
  independent signal; and
- adjudication records the summary, root cause, failure mode, required change,
  category, severity, atomicity, annotators, and disposition.

A changed path or blob at F is only a search signal. It is not sufficient fix
evidence. Stylistic preference, question-only discussion, superseded context,
non-actionable explanation, duplicate root cause, or an issue already absent
at H must be excluded with a reason. Paper-ready exclusions receive the same
independent dual-annotation treatment as inclusions: each declared annotator
must supply a digest-bound `exclude` record before final adjudication.

The checked-in draft has 50 cases and 121 provisional comments across 40/7/3
small/medium/large cases. Mechanical anchoring does not make those comments
gold. The draft remains unscored until every semantic, thread, fix, and
adjudication gate is resolved. It also contains no committed raw GitHub source
archive, authenticated thread artifact, curation packet, or released labels.

For newly sampled cases, `discover` preserves the exact raw GitHub REST pages
behind the candidate window. The artifact binds the canonical endpoint and
descending-created query, per-page/page/max-page policy, safe representation
headers, response and page hash chain, live versus explicit cache-only mode,
versioned rejection/grouping/actionable-hint policy, deterministic candidate
ordering, raw-population digest, rejection accounting, and ordered
candidate-set digest. Its validator reconstructs the candidate set from those
pages instead of trusting stored counts or rankings. A separate linkage binds
the exact ordered draft or released selection and selected comment IDs to that
candidate pool.

This evidence makes the sampled window and selection auditable; it does not
make the window an exhaustive census or turn the lexical actionable hint into
a semantic label. The current checked-in draft records only legacy source
filenames and intermediate filenames in `sampling_provenance`. Those are not
raw-page digests or a discovery-selection linkage, so selection-rate,
prevalence, and exhaustive-coverage claims remain prohibited unless the sample
is rebuilt with the sealed discovery flow.

Context-line comments are not coerced into added-line anchors. A released gold
record declares `lineKind=context` and
`exact_original_commit_and_diff_context_anchor`; added lines declare
`lineKind=added`. Position-based resolution for the one documented historical
off-by-one remains separately identified. LEFT/deleted lines are never eligible.

## Source, thread, and adjudication evidence

`verify-current-comments` is the low-request first gate. It seals each raw,
paginated REST review-comment response, the selected root responses, and the
complete direct reply set for all 50 PRs. Historical H anchors use REST
`original_line` and `original_start_line`; nullable current `line` and
`start_line` are retained separately because later commits can move or
invalidate them. Live mode proves that GitHub answered (or conditionally
revalidated) each request during that run. Explicit offline mode proves only
the integrity and draft binding of the cache and is labelled `cache-only`.
Neither artifact is a gold set or a substitute for the evidence below.

The draft's normalized `line` and `start_line` mean the H/original end and
range-start, respectively. Its `raw_current_line`, `raw_original_line`,
`raw_current_start_line`, `raw_original_start_line`, and `raw_start_side`
preserve the REST projection without overloading those normalized names.
Paper-ready thread evidence also freezes GraphQL `line`/`originalLine`,
`startLine`/`originalStartLine`, `diffSide`, and `startDiffSide`. Runtime
validation requires the GraphQL, REST, normalized-thread, and released-gold
projections to agree field by field. A current null line is valid for an
outdated root; falling back from a missing original coordinate to a current
coordinate, or treating an original range start as a current range start, is a
hard failure.

GitHub REST review-comment endpoints expose roots, replies, and submitted
review records. They do not expose GraphQL review-thread `isResolved` and
`isOutdated`. `archive-draft-sources` preserves the exact REST PR, comment, and
review payloads and rejects selected-comment drift from the draft. A referenced
review is eligible only when it is a human `User` review by the comment's
reviewer, has a submitted state (`APPROVED`, `CHANGES_REQUESTED`, `COMMENTED`,
or `DISMISSED`), has a valid `submitted_at` and full commit SHA, and its REST
`pull_request_url` identifies the same Magento PR. Pending, unsubmitted, and
cross-PR review records are rejected.

The archive is verbatim for the current REST response, not a reconstruction of
historical edits. In this draft, 119 of 121 selected comments have
`updated_at != created_at`; the collected API data does not expose their prior
wording and therefore cannot by itself prove the exact body present at H.
Curation must test the current body against code at H, the complete thread, and
same-root-cause fix evidence. Publications must disclose the unavailable edit
history and must not call these bodies verbatim historical wording.

With an authenticated GitHub token, `collect-threads` paginates GraphQL review
threads, reconciles their database IDs with REST roots/replies, records both
resolution booleans, requires the complete GraphQL message-ID set to equal the
REST root/reply set, checks nested-comment pagination, and binds source and
response digests. The artifact embeds every raw GraphQL page with its canonical
endpoint, POST query/variables, response, prior-page link, and deterministic
request/response/page/archive digests. Validation proves every cursor handoff
and binds each normalized selected thread to its raw node/page; missing pages
and re-sealed raw/normalized mismatches fail closed. Without a token it emits a
REST-only provisional artifact with unavailable resolution metadata;
REST-visible completeness must not be reported as confirmed thread resolution.
Explicit offline collection is different: it can consume the exact cached
GraphQL request/response pages without a token. It validates each cached
request/response digest, fails on a cache miss or mismatch, and never falls
back to the network. Live GraphQL collection continues to require a token.

Paper-ready release consumes the REST source archive, this thread-evidence
artifact, and a curation packet bound to the same draft bytes and canonical
digest. The archive must contain the exact 50 PRs, every selected comment as an
identical member of the full REST comment list, and the submitted review named
by each draft comment. The released selection retains, per case, the archive
digest, case-evidence digest, exact PR-response digest, a digest for every
selected raw comment response, and a digest for every referenced raw submitted
review response. These are the `archiveDigest`, `caseEvidenceDigest`,
`pullResponseSha256`, `selectedCommentResponseSha256`, and
`submittedReviewResponseSha256` fields of `sourceArchiveEvidence`.
Materialization refetches and rehashes the exact objects, retains that case
evidence in the corpus, and strict validation binds each embedded raw response
and normalized record back to it.

For each released root, `reviewThreadEvidence` also contains a digest-bound raw
REST projection made directly from the source archive: the exact root, every
reply, and every submitted-review record referenced by those messages. Release
requires its message IDs to equal the raw GraphQL node IDs and cross-checks
each overlapping field, including URL, author/type, body, timestamps, reply
identity, review identity/state/commit, author association, and original
commit. Strict corpus validation recomputes that three-way
GraphQL/REST/normalized reconciliation offline rather than trusting
`messageIdsReconciledWithRest`. Materialization compares the projection to the
current REST cache and fails if any reply or review is added, removed, or
changed.

The packet must cover the exact ordered draft cases and comments, with matching
B/H/F identities, PR/comment metadata, H `startLine`/`line` ranges, and diff
hunks. Release requires
each checkpoint-to-final path diff to be present and recomputes its SHA-256.
Each packet comment also records a strict `modified`, `renamed`, or `deleted`
transition, the source and final paths, checkpoint/final blob OIDs, and the
50%-policy rename score. Renames retain destination source; deletions retain an
explicit unavailable final-source sentinel rather than an ambiguous empty
window. Release rejects inconsistent status/path/blob/source fields.
Code-change evidence is bound to the packet H..F path-diff digest, and thread
evidence is bound to the canonical thread digest.
Each of two or more independent annotators supplies a digest-checked `accept`
record bound to the case, source comment/body, released decision, REST archive,
GraphQL thread, and curation packet; the declared annotator set must match the
records exactly. Materialization then refetches the full PR, comment, and
submitted-review objects and cross-reconciles their normalized fields. From the
local source clone it independently regenerates every retained H→F path
transition and deterministic diff, including rename resolution and deletion,
and requires exact equality with the released status, paths, blob OIDs, rename
score, and diff digest. Thus a self-consistently re-sealed but fabricated or
stale packet fails before the corpus is written. Materialization also proves
B→H→F ancestry, merge parentage, the reviewed mainline merge base, and merge
reachability from a frozen mainline cutoff before retaining a digest-bound
ancestry record.

Offline materialization is zero-network: API inputs must be cached and the
repository must be an existing clone or linked worktree with every selected
object. It does not clone, fetch, rewrite `origin`, or permit partial-clone lazy
fetches; shallow history, legacy grafts, missing local commits, and missing diff
blobs fail explicitly. Deterministic diffs force text, disable external diff
and text-conversion drivers, and pin color, path prefixes/quoting,
context/inter-hunk context, submodule handling, output indicators, rename
threshold, and rename limit. System/global attribute files are disabled.
Nonempty host-only `.git/info/attributes` and repository-local `diff.*`
configuration fail closed; committed `.gitattributes` remains repository
state, but cannot invoke textconv/external helpers or force binary output.
All local evidence reads ignore replace refs and neutralize the legacy graft
file. Evidence subprocesses first remove inherited `GIT_*` repository,
object-store, index, and config-injection variables; non-Git environment such
as proxy settings remains available for explicit live fetches.

Paper-ready exclusions use the same source-artifact discipline. In addition to
dual independent `exclude` records, the complete GraphQL thread must have
REST-reconciled message IDs, available boolean resolution/outdated states, and
a root ID, body, and update time matching the selected REST comment. The exact
packet comment must match the same ID and body. Both canonical thread and
packet digests are included in every exclusion record. Any missing evidence or
identity/digest mismatch fails the release instead of treating the exclusion as
provisional truth.

## Leakage controls

The benchmark separates evidence, replay, analysis, and judging:

1. Curation retains source PR URLs, reviewer identities, comments, replies,
   H..F evidence, and labels.
2. A label custodian derives a self-digested execution corpus from the strict
   release. Its exact field contract contains only corpus/repository identity,
   ordered opaque case IDs, public partition/size bands, B/H snapshot/diff/path
   identities, and replay refs. Known reviewer/comment, expected-issue,
   adjudication, disposition, decision, and fix values are recursively rejected.
3. Replay PRs use opaque case IDs and reveal only B, H, and the diff digest.
   They omit source PR numbers/URLs, reviewer text, expected issue summaries,
   dispositions, and F.
4. CodeCrow receives the replay B..H diff, source content at H, and RAG context
   indexed at the exact B ref/SHA. Prior findings are empty.
5. The judge receives an independently curated issue and anonymous candidate
   findings. It does not receive model identity as evidence for a match.

Primary replay planning, all replay apply/verification, operator preflight,
and H/F analysis reject the released corpus kind and accept only the
label-free execution corpus. The only pre-unseal exception is the
label-custodian F-plan command: it reads the release to select exact final
commits but emits a recursively label-free plan. Its
`executionCorpusDigest` is propagated through both replay plans, locks,
attestations, registration/seal records, and run manifests, and the runner
copies the exact artifact into each run root. The full released corpus returns
only after unseal for judging and metrics.

Each replay base must be indexed under its unique branch with
`preserve_other_branches=true`; otherwise a later index request can delete
earlier branch snapshots. Immediately before a frozen run, `verify-replay`
performs a live read-only check of the fork identity and every base ref, head
ref, and PR. Its attestation is bound to the corpus digest, execution-corpus
digest, embedded plan, and replay lock. The runner accepts it only when it precedes invocation and is within the
preregistered age window (never more than one hour for paper runs), copies the
execution corpus, lock, and attestation under the run root, and
artifact-integrity validation reopens all three
instead of trusting manifest PR numbers. The attestation captures an
authenticated live check but is not a GitHub cryptographic signature.
The runner is read-only with respect to indexing and
requires exact immutable receipts before and after analysis. A valid receipt
describes a sealed generation manifest: exact workspace/project/branch/commit
and repository revision, member count and point-count relationship, member and
manifest digests, the verified acquisition source-tree digest, repository
facts, unique plugin IDs, and plugin/index fingerprints.

The standalone operator preflight is the fail-closed boundary immediately
before execution. It makes only read-only exact-index, container-inspection,
and local Git calls, then writes one requested readiness receipt. It validates
the label-free execution corpus and primary replay lock; a supplied live replay
attestation and its at-most-one-hour freshness; exact fork, CodeCrow project,
and RAG coordinates; effective analysis/judge requested and expected-response
model IDs; the presence but never values of required credential environment
variables; and the paper runtime-evidence flags. It reopens all 50 B/H Git
snapshots and reconstructs each canonical B source-tree identity without
checking out or changing a ref.

For all 50 locked B refs it calls the same read-only `exact_index_receipt`
consumer used by the runner. Each receipt must match its local B source tree,
and the complete set must use one consistent plugin ID/fingerprint,
descriptor/implementation fingerprint, neutral index representation, and
canonical include/exclude selection policy. The artifact retains safe receipt
projections, set digests, per-check failures, and a digest over the complete
readiness record. Credentials are never serialized. Its `paperReady` flag is
unconditionally false because later analysis, judging, human audit, metrics,
and package gates cannot exist at preflight time. `runReady` alone reports
whether execution may begin, while an explicit post-run protocol-evidence
blocker preserves the publication boundary.

The preflight receipt proves what was indexed; it does not prove what an
analysis retrieved. For paper runs the Redis event stream must contain exactly
one terminal `review_evidence_completed` record showing every registered review
unit completed with at least one registered unit, every deterministic retrieval
state is complete, semantic retrieval is enabled, semantic failures are zero,
and the exact-evidence count is well formed. It must also prove PR indexing and
bind the fork PR, target ref, exact H/B revisions, base-generation manifest,
PR-overlay fingerprint and complete overlay-membership manifest, plus the base
plugin and index-representation identities. The stream must contain no error,
exactly one final event as its last event, the same job identity, and the exact
response submitted to the product finalizer. HTTP compatibility transport has
no such event stream and cannot satisfy this gate.

Model pretraining may still contain public Magento review data. That risk
cannot be eliminated by prompt blinding. A paper must disclose it, avoid source
PR numbers in analysis prompts, and include sensitivity checks such as
temporally newer cases or private holdout evidence when available.

## Product finding boundary

The inference worker response is an intermediate artifact, not the scored
CodeCrow output. For every successful job the runner submits the captured
analysis data and exact frozen head-file contents to the authenticated internal
Java product finalizer. The finalizer reuses normal result validation, issue
mapping, snippet anchoring, actionable-severity filtering, and exact-identity
ingestion de-duplication. It explicitly disables historical issue lookup and
returns transient issues without persistence, snapshot writes, cache use, or
VCS publication.

Only these Java-finalized first-iteration issues enter judging. The run
artifact retains the exact request with credential fields redacted, both output
representations, and raw/final issue counts plus a digest of the finalizer
response. The artifact-integrity gate independently binds that request to the frozen B/H
refs, model/provider, diff, changed paths, empty prior-issue state, and enabled
RAG setting. It regenerates the complete request and enrichment from the
frozen Git objects; exact key-set, source-content, or relationship/statistics
drift fails even when an artifact has been self-consistently resealed. A
model-normalized request-control digest must remain identical across controlled
model comparisons. Secret-like custom analysis parameters are diagnostic
because redaction prevents reconstruction. Failure or contract drift in either
request provenance or finalization makes the case unscored; the runner must
never fall back to raw inference issues.
An external response that contains the literal analysis key, internal service
secret, or a secret-like custom request value is rejected before persistence;
manifest errors replace any known credential with `<redacted>`.

Analysis attempt selection is fixed before execution. A new run requires an
empty output directory, and every requested case ID must resolve exactly.
Confirmatory plans pass their preregistered safe `analysisPlans[].runId` via
`--run-id`; an explicit ID must match exactly on resume. The configured
`project_vcs_workspace/project_vcs_repo_slug` must also equal, case for case,
the owner/name in the replay lock's `forkRepository`. This prevents a
correctly numbered replay PR from being requested through a different CodeCrow
project, and the runner rejects the mismatch before submitting any case.
`max_case_attempts` is part of the digest-bound public analysis configuration
and defaults to one. Each invocation makes at most one attempt per unfinished
case; additional attempts require explicit resume. The manifest retains an
append-only ledger with unique immutable start/result artifacts, request and
result digests, fresh job IDs, errors, retry eligibility, and terminal stopping
reasons. A running attempt left by interruption is closed as interrupted
before another attempt can be appended. Exhausting the cap leaves the case
failed and the run partial. Thus a successful retry cannot erase, overwrite,
or hide an earlier failure.

Resume and paper metrics use the same full ledger validator. It reopens every
start/result artifact for every attempt—not only the terminal success—and
checks the complete artifact field set, identity, digest, request/result
binding, result projection, status, duration, error, termination reason,
retry/stopping transition, contiguous numbering, configured cap, and case
summary. Earlier failed or recovered interrupted attempts cannot be omitted,
deleted, or rewritten with an impossible policy while retaining paper
eligibility.

Confirmatory analysis also requires provider-resolved model-call evidence.
Pre-register both the requested `analysis.model` and the exact
`analysis.expected_response_model` (or pass
`--expected-analysis-response-model`). The inference worker emits one
source-free terminal receipt while retaining the complete opt-in capture in
its configured container directory. The runner copies that capture into the
attempt ledger and verifies every completed model-boundary call, its stage,
provider call count, response digest, and provider-reported model. The
requested, expected, aggregate provider-reported, and per-stage reported roles
remain distinct in the run and metrics artifacts.

The copied capture is accepted only as a direct regular non-symlink child of
the configured directory with a project/PR-bound filename. Its request must
exactly reconstruct the submitted benchmark request after the inference
DTO's fixed defaults and `branch` alias, recursive `[REDACTED]` handling, and
base-URL credential/query removal. This binds custom parameters, enrichment,
project capabilities, and all other prompt-affecting request controls rather
than merely the case/model identity. Missing or duplicate receipts, a model
mismatch, unsafe file identity, or digest/request drift fails the attempt.
Metrics reopen the copied artifact and require identical bindings in the case,
terminal attempt, and raw result. These are auditable local captures, not
cryptographic provider attestations.

## Semantic matching

For each case, the judge evaluates every reviewer issue against every CodeCrow
candidate. A substantive match requires:

- the same underlying defect or harmful practice;
- compatible failure, consequence, or risk;
- a corrective change that would satisfy both reports; and
- grounding in the frozen H snapshot.

Shared path, line proximity, category, or wording alone is insufficient.
Verdicts distinguish `substantive_match`, `partial`, `related_distinct`,
`no_match`, and `unverifiable`.

All substantive edges enter a maximum-cardinality, then maximum-evidence
**one-to-one bipartite assignment**. This prevents one broad candidate from
claiming multiple reviewer issues and prevents duplicate candidates from
claiming the same reviewer issue. Only assigned substantive pairs count as
matches.

Provider/transport retries and structured-output retries are distinct. Invalid
rubric-shaped responses are retained and retried up to a fixed limit.
Successful repeats are atomically checkpointed with case/config/prompt
bindings; completed cases receive their own digest-bound checkpoint. Reusing
the judge output directory resumes only matching checkpoints and rejects stale
or modified ones. Each successful and structurally rejected attempt retains the
exact credential-free chat request and complete parsed provider response
envelope. Artifact-integrity validation reopens those checkpoints, verifies provider model
and response identities, rebuild majority verdicts and maximum-evidence
assignments, and require the raw case projection to match the published
judgment exactly. These captures make fabrication detectable within the
artifact chain; they are not provider signatures. Secret-like custom request
fields are redacted before persistence, and any such redaction fails paper
readiness because the submitted request is no longer exactly reconstructable.
The response model must equal the preregistered
`judge.expected_response_model`; provider routing to another internally
consistent model is not accepted.
The CLI equivalent is `--expected-response-model`. When `--judge-model` is
provided without it, the requested alias becomes the expected response model;
live responses and resumed checkpoints both fail on a missing or different
model identifier.

Every gold-pair prompt retains every candidate. When the full prompt would
exceed `judge.max_prompt_characters`, one deterministic uniform text cap is
applied to candidate titles, descriptions, suggested fixes, path diffs, and
source windows. The prompt records the compaction policy, cap, configured
maximum, and preserved candidate count. Judging fails if even the minimum
complete prompt cannot fit; candidates are never silently dropped.
If a provider response echoes one of those literal credential values, the call
fails before its checkpoint is written.

## Primary metrics

For a scored case or aggregation, let:

- `G` be eligible reviewer issues;
- `C` be CodeCrow candidate findings;
- `M` be one-to-one substantive assignments.

Then:

```text
TP = M
FN = G - M
reference-set FP = C - M
precision = M / C
recall = M / G
F1 = 2 * precision * recall / (precision + recall)
```

The primary precision is **reference-set precision**. Review comments are an
incomplete inventory, so an unmatched candidate is a
`referenceSetFalsePositive`, not a proven-invalid defect. Use this exact
qualification in tables and prose.

Report micro aggregation over issue/finding counts and macro aggregation over
PR cases. Undefined precision for a zero-candidate case remains undefined; it
must not be silently rewritten as 0 or 1. Recall and F1 follow the implementation
definition, and coverage is reported separately.

The confirmatory `primary` aggregate contains only the 20 sealed cases.
`secondary.allCases` and `secondary.development` expose the all-50 and 30-case
development aggregates. Any case with at least one majority pair verdict of
`unverifiable` is excluded from all scoring rather than converted into FN or
reference-set FP. Coverage reports the excluded case, partition, reason, and
unverifiable-pair count.

For artifact-integrity evaluation, every judgment must be supplied together with its
exact analysis-run artifact. Metrics revalidate the run digest/model and every
scored candidate list before setting the analysis-artifact binding fields.
The metrics implementation then enforces machine-verifiable artifact gates: a
strict corpus, full resolved judgment/run and case coverage,
completed Redis runs, immutable analysis/RAG/finalizer identities,
product-finalized finding semantics and finalizer flags, exact redacted
analysis requests, terminal retrieval evidence, sealed-generation receipts,
stable before/after receipts, at least 10,000 bootstrap iterations, and
reconstructable raw judge/provider
checkpoints. It
sets `methodology.artifactIntegrityReady` only when every machine gate passes
and records failures in `artifactIntegrityGateFailures`.

Artifact integrity is necessary but not publication readiness. Metrics may
bind the registration, sealed-access/unseal ledger, and decision-derived
blinded human audit only as an all-or-none set. The protocol validator requires
all registered analysis runs to finish before unseal, reconstructs the complete
gold×candidate decision universe and production maximum assignment, and
requires every subject to receive the preregistered independent ratings and
adjudication. Passing those controls is reported per `protocolControls`.
Metrics still cannot circularly bind the later reproducibility package, and no
post-fix execution is fabricated. A post-fix control-set digest is insufficient:
the metrics command requires and semantically reopens the raw F replay,
analysis runs, judgments, controls, paired H artifacts, and seal context before
validating the post-fix control. The reproducibility verifier then regenerates
metrics from those packaged inputs and the fixed registered bootstrap settings;
all configurations, cases, assignments, aggregates, and pairwise comparisons
must match exactly. Re-sealing a changed JSON digest cannot satisfy that check.

Machine-validated F disappearance outcomes are not a judge calibration. Until
the blinded human audit covers every registered F per-gold decision,
`post_fix_judge_human_audit_not_bound` remains. Consequently
`publicationProtocolReady` and `paperReady` remain false until that genuine
audit and the other remaining controls exist; `publicationProtocolGateFailures`
names them.

Public artifacts sanitize absolute configuration URLs by removing userinfo,
query strings, and fragments. Reproducibility-package validation additionally
scans JSON/TOML, manifest instructions and limitations, and text artifacts for
credential-bearing URLs, literal secret assignments, provider-token patterns,
and private-key markers. Environment-variable placeholders remain permitted;
literal credentials fail before the manifest is written and again on verify.

When multiple analysis models are compared, the metrics gate canonicalizes the
run controls with only the selected review model replaced by a sentinel. It
requires identical provider, public analysis configuration, custom parameters,
retry/failure policy, replay lock, selected cases, index receipts, finding
semantics, transport, and service image identities. Ephemeral container IDs are
excluded from this equality check.

## Novel-finding adjudication

An unmatched candidate may be classified as:

- `valid_in_scope_novel`;
- `invalid`;
- `out_of_scope`; or
- `unverifiable`.

The resulting confirmed-finding precision is secondary. It excludes
out-of-scope, unverifiable, and unadjudicated findings from its denominator. It
does not produce an expanded-gold recall or F1.

For a publishable expanded-gold analysis, pool unmatched findings from **all**
compared models, remove semantic duplicates without model labels, adjudicate
the pooled set independently, and rerun matching for every model against the
same expanded reference set. Never enrich the gold set from one model and
score competitors against that asymmetric set.

## Development and sealed partitions

The release selector uses a deterministic stratified assignment of 30 cases to
`development` and 20 to `sealed`. Development cases may be used to calibrate
prompts, match policy, category mapping, and operational reliability. Sealed
comments, outcomes, and aggregate results must remain inaccessible to
model/prompt tuning until the protocol is frozen.

The released corpus JSON contains both partitions' labels. The sealed
commitment and ledger are procedural, digest-bound custody evidence, not
encryption, a hiding commitment, or trusted timestamping. Named custodians and
storage access control must enforce the separation. The executable publication
bundle uses a preregistered post-unseal `blinded_human_audit`; full-corpus
judgments cannot be counted as pre-unseal development calibration because they
necessarily consume sealed labels.

Use sealed results as the confirmatory primary endpoint. Report all-50 and
development results as secondary aggregates, plus sealed size-band strata. The current
40/7/3 distribution is evidence-driven and imbalanced; large-band estimates
will be especially uncertain and must not be presented as equally powered.
Category and severity slices report reviewer-issue recall only because
unmatched candidates have no reviewer label.

## Uncertainty and comparisons

Confidence intervals use a pull-request-cluster bootstrap: sample PR cases with
replacement and keep every issue/finding within the sampled case together.
This respects within-PR dependence better than sampling individual comments.

Model comparisons use paired bootstrap samples over cases scored in both runs.
Confirmatory comparisons use common scored sealed cases and report paired 95%
intervals for micro precision, recall, and F1 plus macro per-case F1. Always
report common-case coverage, point estimates, intervals, analysis model, judge
model, prompt identity/digest, corpus digest, bootstrap seed, and iterations.
Publication artifact integrity requires at least 10,000 iterations.
These intervals are descriptive; they do not alone establish a causal model
improvement.

## Post-fix controls and ablations

Pre-register a post-fix control that analyzes a separate B→F replay, where F
is the source PR's exact `finalHeadSha`. The label-custodian planner requires
strict B < H < F ancestry for every case, regenerates each B→F diff/path set,
and emits no reviewer or fix labels. Its plan, lock, and live attestation bind
the canonical execution corpus; all later pre-unseal F steps accept only that
projection. After unseal, the judge derives and validates each fix commit and
H→F path transition against local Git. The F ref/PR is distinct from H and
contains no reviewer evidence.

Run F before unseal with the paired H model, public analysis configuration,
case order, retry policy, exact B index-generation receipts, and immutable
analysis/RAG/finalizer image identities. The runner uses the existing B index
plus the F PR overlay and never reindexes. Judge F after unseal with the paired
H judge configuration and registered prompt identity.

For each gold matched at H, report `disappeared`, `still_detected`, or
`unverifiable`; classify H-unmatched gold as
`not_applicable_primary_unmatched`. Do not require zero total findings: F may
contain other valid issues. The disappearance denominator is conditional on H
true positives and includes unverifiable outcomes explicitly. It is not
recall, precision, or causal evidence.

The control set is semantic, not hash-only: metrics must reopen the replay
plan/lock/attestation, H/F runs, H/F judgments, raw F case artifacts, seal
ledger, and every derived control. Until the blinded audit also covers each
registered per-gold F outcome, preserve
`post_fix_judge_human_audit_not_bound` and keep publication readiness false.

Useful controlled ablations include:

- Magento plugin enabled versus neutral PHP analysis;
- exact RAG enabled versus an explicitly documented no-RAG condition;
- retrieval/prompt changes with the analysis model held fixed;
- analysis model changes with prompt, index, and judge held fixed; and
- judge-model/repeat sensitivity on the same frozen candidate outputs.

Every ablation must use identical corpus cases, B/H revisions, replay
identities, index representation, failure policy, and matching rules unless the
named factor is intentionally varied.

# Technical paper protocol

This document is the minimum protocol for using this benchmark as evidence in a
technical paper. It prevents an operationally successful run from being
mistaken for a validated experiment.

The current checked-in 50-case, 121-comment draft is provisional. It is not
eligible for scoring and ships without a raw GitHub source archive,
authenticated thread evidence, curation packet, or released decisions. Follow
the [README workflow](../README.md) and the
[methodology](METHODOLOGY.md) before starting a paper run.
`verify-current-comments` is a preliminary drift gate: it can bind the exact
current REST roots and complete reply sets with 50 paginated PR endpoints, but
its live or explicitly cache-only artifact is never a substitute for the full
pull/review archive, authenticated GraphQL evidence, and human release gate.

## 1. Freeze the study before opening the sealed split

Pre-register and hash:

- the research question and primary endpoint;
- inclusion/exclusion rules and the review-round B/H/F definition;
- released corpus digest, 30-case development split, 20-case sealed split, and
  the 3–10/11–30/31–80 size bands;
- analysis models, provider identifiers, custom parameters, prompt
  identity/digest, exact configuration, exact CodeCrow and plugin
  commits/images, and RAG representation fingerprints;
- Redis transport, terminal retrieval-evidence requirements, deterministic Git
  diff settings, sealed-generation receipt contract, and product-finalizer
  contract;
- judge model, expected provider response model, prompt digest and maximum,
  deterministic prompt-compaction policy, odd repeat count, tie/unverifiable policy,
  one-to-one matching algorithm, and exact credential-free provider
  request/response checkpoints;
- sealed primary reference-set micro precision, recall, and F1; secondary
  all-50/development aggregates; macro and strata analyses; a bootstrap seed,
  at least 10,000 iterations, and paired comparison direction;
- failure, timeout, retry, missing-case, zero-finding, and stopping rules;
- planned post-fix controls, ablations, and secondary novel-finding analysis;
  and
- all claims that will and will not be made.

Do not choose the best model, judge, prompt, threshold, subset, or repeat count
after seeing sealed results. Exploratory changes require a newly labelled
experiment and must not replace the preregistered result.

Use `register-study` to create the digest-bound registration before execution.
Every paper run and judgment uses its exact preregistered `--run-id` and
`--judgment-id`. The executable registration validator fixes the strict corpus,
models/configs/prompts, endpoints, comparison controls, retry/stopping rules,
paired PR-cluster bootstrap seed and at least 10,000 iterations, audit policy,
post-fix plan, and claims.

The released corpus JSON itself contains both development and sealed labels.
The sealed commitment and `seal-study` ledger are procedural, digest-bound
custody evidence; they are not encryption, a hiding commitment, a trusted
timestamp, or proof that no custodian copied the labels. Storage access control
and named custodians remain part of the study procedure. The validator requires
registration before every run, every exact 50-case run to complete before
unseal, dual authorization, and sealed access only after unseal, but it proves
only the internal consistency of the supplied artifacts.

## 2. Release a defensible reference set

For a newly sampled paper corpus, retain and validate the sealed discovery
artifact and its standalone discovery-selection linkage before curation. That
evidence must cover the exact canonical REST endpoint/query/pages, raw response
and page-chain digests, fixed filter and ordering policies, complete candidate
set, and ordered selected PR/H/comment subset. The checked-in draft predates
this mechanism: its filename-only sampling record cannot establish an
exhaustive pool, a selection rate, or immutable selection lineage. A paper
using this draft must disclose that limitation; a claim that depends on
sampling prevalence or exhaustive coverage requires rebuilding the sample.

The provisional draft may be promoted only after:

1. `archive-draft-sources` reproduces and archives the exact GitHub REST
   PR/comments/reviews without selected-comment drift. Each linked review is a
   human submitted review in `APPROVED`, `CHANGES_REQUESTED`, `COMMENTED`, or
   `DISMISSED` state, with valid submission time/full commit SHA and an exact
   REST PR URL binding; pending, null-submission, and cross-PR records fail;
2. authenticated GraphQL thread resolution evidence is complete and archived:
   every raw endpoint/query/variables/response page, prior-page link, and
   request/response/page/archive digest is retained; cursor coverage is exact,
   normalized threads bind to raw nodes/pages, and nested comments reconcile
   exactly with REST message IDs;
3. every included issue is shown to be atomic, actionable, and present at H;
4. review acceptance and a same-root-cause fix after H are supported by code
   evidence plus an independent authenticated review-thread signal;
5. the curation packet covers the exact ordered draft case/comment set and
   identities, retains every non-empty H..F path diff, and records the exact
   `modified`, `renamed`, or `deleted` path transition, H/F paths and blob OIDs,
   and any 50%-policy rename score. Renames expose destination source and
   deletions use an explicit unavailable final-source sentinel. Packet
   validation recomputes the retained diff-string digest; materialization then
   independently regenerates the transition and diff from the local H/F Git
   objects and requires exact status/path/blob/score/digest equality.
   Code-change and thread evidence items carry the resulting exact packet and
   canonical-thread digests;
6. two human annotators independently label every inclusion and exclusion; each
   accepted issue carries digest-checked `accept` records and each exclusion
   carries digest-checked `exclude` records whose identities exactly match the
   declared set; every record is bound to its case/comment/body, decision, REST
   archive, thread, and curation-packet digests. Exclusions additionally require
   a complete REST-reconciled GraphQL thread with resolution metadata and exact
   root ID/body/update time, plus the same comment ID/body in the packet;
7. disagreements are reported and resolved by an identified adjudicator;
8. all 50 cases retain at least one accepted issue;
9. selection and materialization complete without inferred/null passes; and
10. both commands succeed:

```bash
magento2-benchmark release-selection ... \
  --source-archive draft-source-archive.json \
  --thread-evidence thread-evidence.json \
  --curation-packet curation-packet.json \
  --paper-ready
magento2-benchmark validate --corpus corpus.json --paper-ready \
  --required-cases 50
```

For an offline materialization, verify the API cache is complete and the source
is an existing clone or linked worktree. The run must perform no clone, fetch,
`origin` rewrite, or partial-clone lazy fetch; missing selected objects fail the
run.

Record the selection flow: candidate comments, mechanical exclusions, semantic
exclusions and reasons, disagreements, accepted gold count, PR count, and final
size/category/severity distribution. Explain the current 40/7/3 size imbalance
rather than implying balanced sampling.
For a newly sampled corpus, report the discovery and candidate-set digests,
requested/page counts, short-page versus max-page termination, rejection
counts, and discovery-selection-linkage digest. A max-page termination proves
only a fixed recent window, not an exhaustive repository census.
Materialization must also record successful B→H→F ancestry, two-parent merge
identity, reviewed mainline merge-base equality, and merge reachability from
the frozen mainline cutoff, and must cross-reconcile the embedded full REST
PR/comment/review objects with every normalized field. The released selection
and corpus retain a per-case source record containing the archive digest,
case-evidence digest, raw PR-response digest, and maps of raw selected-comment
and submitted-review response digests: `archiveDigest`,
`caseEvidenceDigest`, `pullResponseSha256`,
`selectedCommentResponseSha256`, and `submittedReviewResponseSha256`.
Materialization refetches and rehashes each object; paper validation rejects a
missing object, changed response, malformed digest map, or archive/case binding
mismatch.

The REST archive pins current bodies and update timestamps, not prior body
versions. Because 119 of 121 draft comments have
`updated_at != created_at`, require curators to validate their current text
against H, the complete thread, and fix evidence. Disclose unavailable edit
history and never describe the current body as proven verbatim wording from the
review instant.

JSON Schema validation is useful for interchange but is not the paper release
gate. The Python validator enforces cross-case identities, digests, exact
snapshot relationships, and the stricter paper-ready mode.

Use the fixed diff policy throughout: Myers, indent heuristic off, external
diff off, and 50% rename detection. Record the Git version. Both added and
unchanged context anchors are eligible only on the RIGHT side and must retain
their explicit `lineKind`/validity method; LEFT/deleted anchors remain excluded.

H-coordinate provenance is evaluated independently of GitHub's current
projection. The historical endpoint is REST `original_line` with nullable
`original_start_line` at `original_commit_id`; REST `line`/`start_line` and
`commit_id` are retained as current fields and may be null or shifted.
GraphQL thread evidence freezes both coordinate pairs and their end/start diff
sides. Release fails if REST, GraphQL, normalized thread, or released gold
substitutes current for original coordinates, omits a raw coordinate field, or
disagrees after digest recomputation.

## 3. Calibrate without contaminating evaluation

Use development cases to:

- calibrate the issue rubric and judge prompt;
- measure inter-annotator/judge agreement;
- establish retry and malformed-output handling;
- verify matching with adversarial pairs such as same-file/different-root-cause
  and broad/duplicate findings; and
- test index, replay, and runner reliability.

Freeze all choices before unsealing. Judge calibration examples must not expose
sealed source comments. If a human audits sealed judge outputs, define the
audit rate and override policy in advance and report every override.

The current executable sealed-publication bundle accepts the second route:
`blinded_human_audit` preregistered on at least five cases, performed after
unseal. `development_calibration` remains diagnostic unless it uses a separate,
dev-only judgment/corpus artifact that cannot read sealed labels; a
full-corpus judgment cannot safely serve as pre-unseal calibration evidence.
Use `judge-evaluation-packet` to export opaque subject IDs, evidence, allowed
verdicts, and the rubric without model, judgment, or machine-verdict identity.
Two or more independent model-blind raters must cover every derived subject,
and an independent adjudicator must resolve every disagreement.

Public Magento code and reviews may occur in model pretraining. Treat this as a
declared contamination risk, not as evidence of leakage or its absence.
Analysis prompts and replay PRs must remain source-review-blind.

## 4. Pin replay and retrieval state

Before the blinded operator receives any inputs, a label custodian must run
`execution-corpus` against the strict released corpus. The resulting
`codecrow-magento2-analysis-execution-corpus` contains only ordered public case,
partition/size, B/H snapshot/diff/path, and replay-ref identities. It excludes
all reviewer/comment, expected-issue, adjudication, disposition, decision, and
fix material and is sealed by `executionCorpusDigest`. Primary replay planning,
all replay apply/verification, operator preflight, and both pre-unseal analysis
lanes consume this projection and reject the released labeled corpus kind. The
sole exception is the label-custodian `post-fix-replay-plan` step: it reads the
release to select exact F commits, emits a recursively label-free plan, and
binds that plan to the canonical execution-corpus digest. Its lock and live
attestation propagate the same binding.

Create a replay plan from that execution corpus, inspect it, and apply it to the
exact confirmed fork. Save the execution-corpus, plan, and lock digests. Do not
force-update divergent benchmark refs.
Immediately before analysis, run `verify-replay` with authenticated GitHub
access and preserve its corpus/lock-bound observation of the fork repository,
all 50 base/head refs, and all 50 PR identities and SHAs. Supply that
attestation to every model run; the runner copies it and the replay lock into
  the run directory, and the artifact-integrity gate validates both copies. Its collection
time must precede run invocation and remain inside the preregistered freshness
window, capped at one hour. The attestation is a digest-bound capture of an
authenticated live query, not a GitHub-signed statement.

Index each of the 50 unique base refs at its exact B SHA outside the runner.
Every index request must use `preserve_other_branches=true`; the default false
would remove previously indexed branch snapshots. Capture a revision receipt
for every ref after all indexing completes.

Before a paper run, verify each receipt contains:

- the requested `workspace`, `project`, `branch`, and full `commit`, with an
  identical `repository_revision`;
- `generation_schema = "codecrow.repository-index-generation"`;
- a positive `generation_member_count` and
  `point_count = generation_member_count + 1`;
- valid `generation_members_sha256`, `generation_manifest_sha256`,
  acquisition-bound `source_tree_sha256`, and `repository_facts_sha256`;
- unique `plugin_ids` containing the required `php` and `magento` IDs; and
- valid `plugin_fingerprint`, `plugin_descriptor_fingerprint`,
  `plugin_implementation_fingerprint`, and
  `index_representation_fingerprint`.

Before starting any registered run, execute the standalone read-only operator
preflight with the label-free execution corpus, primary replay lock, fresh live replay
attestation, local Magento clone, and final paper configuration. Preserve its
digest-bound JSON artifact. The gate must prove all 50 B/H snapshots and local
B source-tree identities; exact fork/project/workspace coordinates; all
required credential variable names and presence without persisting values;
effective requested and exact expected-response model IDs for analysis and
judge; inspectable running inference/RAG/finalizer images; and all 50
exact-index receipts. The receipt set must have stable plugin,
descriptor/implementation, representation, and canonical selection-policy
controls, while every generation/source-tree binding remains case-specific and
exact.

The preflight must not index, enqueue, analyze, judge, finalize findings, create
refs/PRs, or otherwise repair failed state. A missing optional attestation is
diagnostic and forces `runReady=false`; paper execution requires a valid
attestation no older than the registered window. A true `runReady` is only
permission to begin the registered run. Preflight `paperReady` is always false,
with an explicit post-run protocol-evidence blocker, because runtime
retrieval/model receipts, H/F outcomes, human audits, metrics, and the
publication package cannot be proven before execution.

Set `analysis.require_exact_index = true` and
`analysis.require_runtime_provenance = true`. Also keep
`analysis.require_retrieval_evidence = true`. The runner must see identical
sealed-generation receipts before and after analysis and, for each case,
exactly one terminal retrieval event proving all registered review units
completed with at least one registered unit, deterministic states are complete,
semantic retrieval is enabled, semantic failures are zero, and the
exact-evidence count is valid. The event must also prove PR indexing and bind
the fork PR, target ref, H/B revisions, sealed base-generation manifest, and PR
overlay fingerprint and membership manifest. It also binds the base plugin and
index-representation identities. The Redis stream must have no error, exactly
one final event in last position, the same job identity, and the exact response
passed to finalization. Any missing, incomplete, duplicate, disabled, unbound,
or changed evidence invalidates that run; do not silently reindex and resume
under the same run identity.

Paper runs require Redis transport because the legacy HTTP compatibility route
does not expose terminal retrieval events. An HTTP run is a smoke test even if
all other inputs are identical.

## 5. Execute controlled analysis and judging

For every analysis configuration:

- use the same released corpus, replay lock, exact base indices, case order,
  timeout/retry policy, CodeCrow prompt/config, and empty prior-findings input;
- vary only the preregistered factor;
- archive each exact request with credential fields redacted, its digest, raw
  inference responses, Java product-finalizer responses, normalized finalized
  findings, durations, errors, model/provider parameters, and immutable
  inference/RAG/finalizer service identities;
- retain a model-normalized request-control digest and independently regenerate
  every request, including enrichment content and statistics, from the frozen
  Git B/H objects when computing paper metrics;
- reject secret-like analysis custom parameters from paper comparisons because
  their redaction prevents exact request reconstruction;
- fail before persistence if an analysis, event-stream, finalizer, or judge
  response echoes a literal credential or secret-like custom request value;
- require the authenticated finalizer to report validation complete,
  persistence/publication false, and previous-issue state unused; and
- report partial runs and coverage. Do not convert failed cases into
  zero-finding cases.

If analysis is resumed, require an exact match on the manifest digest,
corpus/model/provider, transport, public config digest, replay-lock digest,
runtime service identities, finding semantics, ordered selected cases, exact
index receipts, and every attempt-ledger artifact digest. New runs require an
empty output directory. Pre-register `max_case_attempts`; the default is one,
each invocation may append at most one attempt per unfinished case, and only
explicit resume may retry. Preserve unique immutable start/result artifacts,
failed and interrupted attempts, fresh job IDs, retry eligibility, and the
terminal reason (`case_completed`, `explicit_resume_required`, or
`attempt_limit_reached`). A successful retry must not replace its failed
predecessor, and reaching the cap must not submit another request. Treat any
identity or artifact mismatch as a new run rather than weakening the resume
check.

Execute each preregistered `analysisPlans[].runId` with `run --run-id`; reject
unsafe identifiers and require an explicit ID to match the existing manifest
on resume. Before any case submission, require the configured CodeCrow project
VCS owner and repository slug to equal the replay lock's `forkRepository`
owner/name exactly, including case. The project must target the replay fork,
not upstream `magento/magento2`.

The metrics gate must call the same complete attempt-ledger validator used by
resume. Reopen every start and result artifact and validate its exact field
set, identity and digest, request/result bindings, result projection, status,
duration, error, termination reason, retry eligibility and stopping reason.
Validate all earlier failed and recovered interrupted attempts, contiguous
numbering, the fixed cap, and the terminal case-summary binding. Reject omitted
history, missing historical artifacts, rewritten identities, and
self-consistently re-digested but policy-impossible retry/stopping states.

Pre-register the analysis routing alias and exact provider-resolved response
model separately. Paper runs must set
`analysis.require_model_call_evidence=true`, use Redis, and enable the
inference quality capture only for the benchmark project ID. Preserve exactly
one terminal capture receipt per successful attempt and copy its complete
artifact from the configured analysis container into the immutable attempt
directory. Require every completed provider call to report the expected model,
and retain requested, expected, aggregate provider-reported, and per-stage
model roles.

Reject a missing or duplicate receipt, path outside the exact configured
capture directory, symlink or non-regular copy, wrong project/PR filename,
digest drift, incomplete provider-call ledger, or wrong reported model. Also
compare the complete captured inference DTO request to the exact benchmark
request after documented secret/URL normalization, fixed DTO defaults, and the
`branch` serialization alias. This comparison includes model custom
parameters, enrichment, project capabilities, rules, task context, and every
other non-secret request field. Reopen and revalidate the copied capture during
resume and metrics generation, and bind it identically in the case, terminal
attempt, and raw result. Report that this evidence is an auditable local
capture rather than a provider signature.

Judge frozen outputs separately from analysis. The judge must evaluate every
gold/candidate pair and use one-to-one substantive assignment. Archive stored
per-repeat parsed judge responses, usage metadata, response model identifiers,
prompt hashes, repeat agreement, assignments, and unverifiable results.

The client persists the exact system prompt, exact user prompt, parsed JSON
response, exact credential-free provider request, and complete parsed provider
response envelope for every repeat, in addition to normalized majority
decisions, response hashes, provider response IDs, model IDs, and usage
metadata. The artifact-integrity gate reopens those call artifacts and reconstructs the
published case output. It also requires the returned model to equal the
preregistered `judge.expected_response_model`; a different provider-resolved
model is a failure. Treat the envelopes as auditable captures rather than
cryptographic provider signatures. Secret-like custom request values are
redacted before persistence; because such a request is no longer exactly
reconstructable, it cannot pass the paper-ready gate.

The equivalent CLI option is `--expected-response-model`. When
`--judge-model` is passed without it, the requested alias is also the expected
response model. Pre-register any distinct provider-resolved identifier.
Gold-pair prompt compaction must retain every candidate, apply one deterministic
uniform cap across candidate text/evidence fields, and archive its explicit
policy and cap. If a complete prompt cannot fit, fail the case rather than
sampling or dropping candidates.

Pre-register both provider-call retries and structured-output retries. Retain
every rejected structured response and validation error. Preserve atomic
per-call checkpoints and completed case checkpoints; each must be bound to the
case input, judge config, system/user prompts, and its declared digest. Resume
only a byte-valid matching checkpoint and fail on stale/tampered state.

For primary comparisons, use the same fixed judge configuration for every
analysis model. Report sensitivity to at least one alternative judge
configuration or a preregistered human audit. Judge disagreement is uncertainty
in the measurement process, not analysis-model error.

## 6. Report metrics with the correct meaning

Primary counts are:

```text
TP = one-to-one matched reviewer issues
FN = eligible reviewer issues without a match
reference-set FP = candidate findings without a reviewer-set match
```

Call precision **reference-set precision** and describe unmatched candidates as
reference-set false positives. Do not state or imply they are invalid defects.
Report secondary confirmed-finding precision only with its denominator and
excluded verdict counts. Do not report secondary recall/F1 until a pooled,
model-blind novel set has been independently adjudicated and all models have
been rematched against it.

Supply one `--analysis-run` artifact for every judgment when building the
paper metrics, and supply the frozen Magento clone with `--repository-path` so
analysis requests and judge code evidence can be regenerated. The exact set of
run IDs must match the judgment set. Metrics
must revalidate each run digest, analysis model, completed scored case, and
candidate list before `analysisArtifactsBound` and
`artifactIntegrityReady` may be true. The metrics artifact-integrity gate also
checks the strict corpus, full resolved judgment/run and case coverage,
completed Redis runs, immutable analysis/RAG/finalizer
identities, product-finalized finding semantics and finalizer flags, exact
redacted requests bound to every frozen B/H diff, terminal retrieval evidence,
sealed-generation receipts, stable before/after receipts, and at least 10,000
bootstrap iterations. It emits every unmet machine condition in
`methodology.artifactIntegrityGateFailures`.

The executable artifact gate does not prove protocol completion. Metrics accept
the registration, seal ledger, and judge evaluation only as an all-or-none
set. Their validator reconstructs all corpus/run gold and candidate
projections, requires every corpus case, the complete gold×candidate pair
matrix, the production maximum assignment, and exact unmatched/novel
decisions, then requires the human records to cover that deterministic subject
universe exactly. It reports both human–human reliability and final
judge–human agreement/confusion. Passing those controls removes their named
missing-artifact failures. A semantically valid verified-F execution still
does not calibrate its own derived judge outcomes:
`post_fix_judge_human_audit_not_bound` remains until the blinded audit covers
every registered per-gold F outcome with independent raters and disagreement
adjudication. `publicationProtocolReady` and `paperReady` therefore remain
false until that audit and the finalized reproducibility package exist.
Remaining controls are explicit in
`protocolControls` and `publicationProtocolGateFailures`.

Every result table must include:

- corpus and metrics digests;
- analysis and judge model identifiers;
- prompt/config and service/plugin/index provenance;
- scored/total cases and failure reasons;
- sealed confirmatory micro and macro point estimates, with all-50 and
  development aggregates labelled secondary;
- pull-request-cluster 95% bootstrap intervals, including paired sealed micro
  precision/recall/F1 and macro-F1 delta intervals;
- size-band and partition results;
- common-case count for paired comparisons; and
- the bootstrap seed and iteration count.

Exclude an entire case from scoring when any majority pair verdict is
`unverifiable`; report it as an uncertainty/coverage exclusion, not as FN or
reference-set FP. Show development and sealed results separately. Category and severity slices
are reviewer-issue recall analyses, not precision estimates. With only three
large cases in the current draft distribution, large-band estimates require
prominent uncertainty and must not support strong subgroup claims.

## 7. Controls and robustness

Run the preregistered post-fix control at the source PR's exact
`finalHeadSha` (F). A label custodian creates the label-free, execution-corpus-
bound F plan; all later pre-unseal F steps consume only the execution corpus.
Before unseal, create distinct immutable B→F refs/PRs and complete F analysis
using the paired H model, public configuration, exact B index-generation
receipts, case order, retry policy, and immutable runtime image identities.
Require strict B < H < F ancestry and locally regenerated B→F diffs and paths.
After unseal, derive and validate the fix-commit and H→F path-transition
bindings, then judge F with the paired H judge configuration.

Report whether each gold issue matched at H is `disappeared`,
`still_detected`, or `unverifiable`; report H-unmatched gold separately as
`not_applicable_primary_unmatched`. The disappearance rate is conditional on H
true positives and is not recall. Do not assert that all F findings should
disappear, and do not interpret disappearance as causal impact.

Metrics and package verification must semantically reopen the raw F replay,
run, judgment, and control artifacts; a self-consistent control-set hash is
insufficient. Machine execution may complete while the explicit
`post_fix_judge_human_audit_not_bound` publication blocker remains.

Recommended sensitivity analyses:

- judge model and repeat count on identical candidate outputs;
- manually audited samples of matched, unmatched-gold, invalid, novel, and
  unverifiable decisions;
- Magento plugin and RAG ablations;
- alternate match-confidence presentation without changing substantive
  assignment eligibility; and
- exclusion of cases with incomplete/ambiguous provenance as a labelled
  sensitivity analysis, not a replacement primary result.

For the primary multi-model comparison, the executable artifact-integrity gate replaces
only the selected review model in its control fingerprint. Provider, custom
parameters, retry/failure policy, replay lock, case order, index receipts,
transport, product semantics, and immutable service images must remain
identical. A run that varies another factor belongs in an explicitly named
ablation and cannot pass the primary artifact-integrity comparison gate.

If a novel expanded reference set is reported, pool unmatched findings from all
models first, blind model identity, deduplicate semantically, use independent
human adjudication, freeze the expanded set, and rematch every model.

## 8. Permitted claims

With a completed protocol, claims may describe observed performance on the
named, hash-pinned Magento 2 core review-round corpus, for the exact CodeCrow,
model, judge, plugin, and RAG configuration tested.

Do not claim:

- performance for Magento 2 contributions generally from this non-random,
  review-comment-conditioned sample;
- that every unmatched finding is a true false positive;
- causal superiority from an unpaired comparison or a descriptive interval;
- production cost or latency improvements without paired measurement on the
  fixed corpus and disclosed infrastructure;
- precision, recall, F1, FP, or FN from the provisional draft;
- a category/size advantage unsupported by its case count and uncertainty; or
- absence of pretraining contamination; or
- that a current archived REST body proves the exact pre-edit wording at H.

Use “higher/lower on this frozen corpus” for descriptive comparisons. Reserve
“improves” or “outperforms” for a preregistered paired design with the same
eligible cases, controlled non-model factors, a reported interval, and no
post-hoc configuration selection. Even then, scope the statement to the tested
corpus and configuration.

## 9. Reproducibility archive

Publish or escrow, subject to provider and privacy constraints:

- this module's source commit and environment lock/image identities;
- draft, generated REST source archive/cache, authenticated GraphQL
  request/response cache and thread artifact, curation packet, annotator
  decisions/records, released selection, corpus, replay plan/lock, and all
  digests;
- exact sealed-generation index receipts, generation digests, and the index
  request log proving `preserve_other_branches=true`;
- analysis/judge public configs, raw/finalizer/retrieval artifacts, and atomic
  analysis attempt ledgers with every immutable start/result artifact, and
  atomic judge call/case checkpoints;
- exact analysis runs bound to judgments, metrics JSON, dashboard bundle,
  bootstrap inputs, and preregistration;
- an artifact manifest with SHA-256 for every file; and
- a limitations statement documenting missing/deleted GitHub data, unavailable
  review-comment edit history, selection bias, contamination risk, judge
  uncertainty, failures, and protocol deviations.

Secrets, private tokens, and provider credentials must not appear in the
archive.

Use `reproducibility-package` only after metrics and dashboard generation, then
run `verify-reproducibility-package`. The package requires the exact released
corpus plus registration, seal, human evaluation, metrics, dashboard,
analysis, judgment, runtime, and public config categories. It also requires
five semantic evidence categories: source discovery/linkage plus raw archives,
curation packet/decisions/selection, the live H replay, a live
current-comment attestation, and the complete registered F replay/run/judgment/
control chain. It rejects symlinks, path escape, overlapping category
ownership, digest drift, unrelated corpus or dashboard data,
private-key/token patterns, sensitive JSON/TOML values, and plain
dotenv/YAML/INI secret assignments.

Verification regenerates the released selection and checks its corpus
projection, validates H/F pairs and controls, and rebuilds metrics with the
registered bootstrap seed and iteration count. All semantic metrics content
except the generation timestamp must match exactly; `metricsDigest` is not
trusted as proof of derivation. A raw F control set still does not provide
human calibration: until the blinded audit covers every F per-gold outcome,
`post_fix_judge_human_audit_not_bound` remains and publication readiness is
false. The package binds finalized metrics; metrics intentionally cannot bind
the later package manifest. Publish explicit rerun instructions and
limitations with it.

Treat the package manifest itself as a public artifact. Scan its rerun
instructions, limitations, root label, and category paths as well as every
packaged JSON/TOML/text file. Reject URL userinfo, secret-bearing query
parameters, unsafe fragments, literal credential assignments, token patterns,
and private-key markers; permit only explicit environment-variable
placeholders. Apply the same checks during package construction and
independent verification.

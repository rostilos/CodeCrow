# Magento 2 benchmark corpus data

This directory currently contains a **provisional, unscored** 50-PR corpus
draft and the exact SQL for a separate automatic 54-case reference-set
workflow. The provisional draft is not a released golden benchmark and must
not be used to report precision, recall, F1, false-positive, or false-negative
results.

The `provisional_unscored` decision-template status and
`provisional_unscored_anchor_validated` corpus status mean that mechanical
replay checks passed while semantic labels and scoring eligibility remain
unset.

Every provisional-draft case is an immutable historical review checkpoint from
a pull request merged into `magento/magento2:2.4-develop`. The included review
comments remain excluded from scoring until their null adjudication fields are
completed and the release blockers below are closed.

## Automatic reference-set artifacts

`magento2-candidate-query.sql` is the checked-in ClickHouse JSONEachRow
acquisition query for the automatic lane. Its checked-in candidate input is
`magento2-candidates.jsonl`. The builder writes the three JSON outputs below;
the release directory also carries their checksum set:

- `magento2-automatic-corpus.json`: the selected, digest-bound 54-case corpus;
- `magento2-automatic-corpus-audit.json`: all acceptance/rejection counts,
  failed gates, selection identities, and input digests;
- `magento2-selected-root-evidence.json`: the 54 raw official GitHub REST root
  responses plus sealed legitimacy replies required by selected flat
  `author_acknowledged_fix` cases, all bound to the final corpus;
- `magento2-automatic-artifacts.sha256`: the release checksum set covering the
  acquisition query, candidate export, corpus, audit, and root evidence.

These output names describe the automatic output contract. A filename alone
does not establish that a build succeeded: pass all six release-set paths to
`validate-automatic`. A failed build writes the audit but does not replace the
corpus or root-evidence outputs. Prior successful outputs can therefore coexist
with a new failed audit; the cross-artifact validator detects that mixed release
directory.

The deterministic selector requires 54 unique PRs and one reference comment
per PR, with 18 cases in each small/medium/large size band and 18 in each
simple/moderate/complex complexity band. It also applies area, reviewer, and
date-band diversity caps of 12, 8, and 42 respectively. After official REST
qualification and exclusion of commits reachable only as dangling local objects,
42 is the lowest feasible date-band cap for this frozen pool. The current
selection contains 42 `legacy_through_2021`, 9 `middle_2022_2024`, and 3
`recent_2025_plus` cases. The builder refuses to weaken these constraints when
the qualified pool is insufficient.

The selector enumerates deterministic 3×3 size×complexity quota matrices,
runs a cell→PR→date extracting max-flow for unique PRs and the date cap, and
then performs bounded minimum-remaining-values (MRV) case selection for the
area and reviewer caps.

Prepare the Git object store used by automatic materialization with:

```bash
magento2-benchmark prepare-repository \
  --repository-path /path/to/magento2-evidence.git
```

The command creates a bare repository when the destination is absent or empty,
or verifies that an existing repository's `origin` is the canonical official
HTTPS `https://github.com/magento/magento2` remote. SSH origins are rejected.
It also rejects repository/worktree URL rewrites, custom upload-pack or SSH
commands, remote-specific proxies, external config includes, and local HTTP
proxy, TLS, or header overrides before any fetch. Every preparation Git command
disables local hooks, including reference-transaction hooks that could otherwise
rewrite fetched refs. It refreshes official branch refs at
`refs/remotes/origin/*`, maps current official PR heads to
`refs/benchmark/pull/*`, prunes removed refs, and checks the complete local
object closure. Existing partial clones receive an unfiltered refetch. The
command also rejects shallow history, object alternates, and symlinked Git
metadata. Fetch/prune may leave unreachable objects in an existing store; the
builder ignores mere object resolution and qualifies EventBase E, H, F, and a
merge commit only through their required retained official refs.

The complexity bands are simple 0–2, moderate 3–5, and complex 6–9. Their
orthogonal score excludes changed-file count because size already owns that
axis. Complexity instead measures line churn, module breadth, change-type
breadth, API/schema/config changes, and production-test coupling.

Before setting `scoringReady: true`, the builder seals the SQL and candidate
JSONL digests; resolves the exact B, review head H, and merged final head F in a
local non-shallow Git repository; verifies B as
`merge-base(H, eventBaseSha)`, the B..H manifest/diff, right-side anchor,
analyzable path, and H..F tree/path transition; requires EventBase E, H, and F
to be reachable from the official named `snapshot.eventBaseReachabilityRef`,
`snapshot.headReachabilityRef`, and `sourcePr.finalHeadReachabilityRef`;
requires E and the merge commit to use the retained branch corresponding to the
official target through `snapshot.eventBaseReachabilityRef` and
`sourcePr.mergeCommitReachabilityRef`; and accepts exactly one of these
objective evidence tiers:

- `author_acknowledged_fix`
- `changes_requested_then_approved`
- `reviewer_later_approved_anchor_changed`
- `changes_requested_anchor_changed`
- `github_suggestion_applied`
- `explicit_code_change_applied`
- `php_return_type_added`
- `actionable_anchor_change_applied`

The complete release validator derives the candidate evidence mode from the
sealed JSONL row structure instead of trusting the corpus or audit label. It
rejects mixed flat/embedded files and requires both declarations to match the
derived mode. In flat mode every selected case remains bound to its exact
one-based source line and canonical row SHA-256; flat author-acknowledgement
cases additionally retain the sealed official reply fields. The `_line` key is
reserved for the reader's synthetic one-based coordinate and is rejected if it
appears in candidate input. Validation reconstructs the deterministic flat pull
and root objects from that row and matches all non-Git row-derived corpus
fields—including PR metadata, E/H/F/merge, historical comment/anchor fields,
and both candidate object digests—rather than accepting a digest pointer with
an unrelated projection. Per-comment rejection receipts use that comment's own
one-based row even when several rows are coalesced into one PR/event-base work
unit; release validation rechecks both the PR and comment identity at every
recorded rejection line. It also proves that accepted candidate cases plus
non-REST rejection receipts account for every flat input row exactly once.
Official REST failures happen after materialization, so their receipts must
overlap the accepted partition and are excluded from that sum.

For flat ClickHouse `author_acknowledged_fix` evidence, the alleged author
reply is not accepted from the mirror alone. The builder fetches the reply via
the official REST comment endpoint and requires a sealed HTTP-200 envelope,
the exact acquired reply ID/body/timestamp/author, the selected root as its
parent, and the same official PR. Root-only hydration does not make this tier
eligible.

The automatic candidate scope permits PRs targeting `2.4-develop`,
`2.3-develop`, `2.2-develop`, and `develop`; there is no explicit branch quota.
H is not required to be an ancestor of F because the PR branch may have been
force-pushed after review. The `actionable_anchor_change_applied` tier requires
strong actionability language in the review and proof that the H..F diff
removed text on the exact reviewed old-side line range.

`snapshot.eventBaseSha` is the repository state used for historical AST/RAG
indexing, and `snapshot.eventBaseReachabilityRef` binds it to the retained
official target branch. B is used only as the left side of the B..H review diff. Each
`headReachabilityRef` is restricted to the matching official PR head or an
allowed retained official branch ref; a review head available only as a
dangling object is not reproducible enough for selection.
`finalHeadReachabilityRef` must be the matching PR-head ref and its prepared tip
must equal F; a retained branch containing F is not a substitute. Merge
evidence is stricter:
`2.4-develop` uses retained `refs/heads/2.4-develop`, `2.3-develop` uses
`refs/heads/2.3`, `2.2-develop` uses `refs/heads/2.2`, and historical `develop`
uses `refs/heads/2.4-develop`. Diff generation is binary-safe (`--binary
--no-text`) and the changed-path manifest is NUL-delimited. The candidate
export's PR body is retained as `sourcePr.body` for the review request instead
of being replaced with reference prose.

The target-reachable merge commit must also equal F, have F as a direct parent,
or have F's exact tree. This admits fast-forward, direct merge, and squash-like
results while rejecting a re-sealed case that substitutes an unrelated target
commit.

After shared release validation, the harness preserves the four canonical
recorded reachability refs and independently fetches E, H, F, and merge into
separate private per-PR refs. It proves every coordinate against that freshly
fetched official ref, requires the fetched PR-head tip to equal F, repeats the
merge/F association rule, and rechecks `B = merge-base(H, E)`. It then stages
eventBase as a private regular-file snapshot read directly from the Git object
database; it does not trust a checked-out worktree.
H source and the agentic H archive are also read from exact Git blobs. EventBase
supplies AST/RAG state, H supplies reviewed source, and B remains only the left
side of the verified B..H diff.

It then verifies every selected root against the official GitHub REST response.
A selected reply, non-human author, or drift in comment/PR identity, URL,
timestamp, path, or original commit is rejected and the balanced set is
refilled deterministically. Provider-mutable current body, reviewer login, and
returned original line/range/side never overwrite the event-time golden fields
that earned legitimacy. `officialRestProjection` stores their current
deterministic projection (body digest, reviewer, returned original anchor,
side, and review ID) plus `historicalDriftFields`; only the otherwise unavailable
review ID is populated on the golden comment. The objective acceptance proof
therefore remains bound to the historical acquisition row; body-derived
actionability terms and anchor-range evidence are validated against that
historical gold rather than the current projection. The full current REST root
responses and their complete GET cache envelopes are retained in the separate
evidence artifact rather than trusted only from the public event mirror. A flat
author-fix record also stores its full sealed reply under
`legitimacyReplyEvidence`, with the reply response digest bound into the case.
Live and `--offline` modes resolve roots and these reply dependencies through
the same GitHub cache/client path; the audit request count includes both. Each
envelope binds method, official URL, status, canonical fetch time, selected
headers/ETag, response digest, and envelope digest. Live mode
conditionally revalidates only a valid envelope; explicit `--offline` mode
rejects missing, legacy, malformed, or digest-mismatched entries. Before its
balanced selection, cache-only mode also requires each eligible envelope to be
status 200 and to contain the exact expected official human root identity;
cache-file existence alone never qualifies a candidate. Cache-only replay
proves byte-level reproducibility, not that GitHub served the evidence again at
rebuild time. The corpus therefore uses stable sealed-response provenance while
the audit records the actual hydration mode.

The automatic corpus is intentionally `paperReady: false`. It is suitable for
reference-set precision, recall, and F1 only after all gates pass. It does not
provide complete conversation evidence, independent human adjudication, or an
exhaustive technical-issue inventory. Therefore an unmatched finding is a
reference-set false positive, not proof of a true technical false positive.
The provisional workflow and its stricter paper-release blockers remain below.

Verify the complete automatic release directory with one command:

```bash
magento2-benchmark validate-automatic \
  --corpus magento2-automatic-corpus.json \
  --root-evidence magento2-selected-root-evidence.json \
  --audit magento2-automatic-corpus-audit.json \
  --acquisition-query magento2-candidate-query.sql \
  --candidates magento2-candidates.jsonl \
  --checksum-manifest magento2-automatic-artifacts.sha256
```

The validator checks the audit's self-digest and success state, source
filenames/bytes/digests/row count, selected IDs and distribution, corpus and
root-evidence digests, and an exact checksum-manifest entry set. All six inputs
are required; validating only the two JSON payloads cannot detect a stale audit
or mismatched acquisition input.

## Files

- `corpus-draft.json` contains 50 unique PR checkpoints and 121 provisional
  root review comments with raw comment, commit, diff-hunk, anchor, and merge
  provenance. These embedded fields are not the complete raw GitHub source
  archive required for release.
- `corpus-draft-audit.json` records the mechanical validation results and the
  gates that still prevent release.
- `curation-decisions.template.json` contains one unlabelled decision entry for
  each of the 121 comment IDs. All semantic, thread, fix, classification, and
  adjudication placeholders are `null`, and every `include` value is `false`.
- `corpus-draft.sha256` pins the exact corpus bytes.

The draft distribution is 40 small checkpoints (3-10 changed paths), 7 medium
checkpoints (11-30), and 3 large checkpoints (31-80). The requested
25/15/10 distribution was relaxed because recoverable, substantive review
evidence was scarcer in the medium and large bands. Evidence quality and exact
replayability took precedence over filling a quota with rejected or
unrecoverable comments.

## Mechanical validation

The audit verifies all 50 cases are unique PRs merged into `2.4-develop`, use a
single review checkpoint, are ancestors of their final PR heads, and have
verified mainline merge parentage. All 121 included comments are root,
right-side comments from a human reviewer other than the PR author. Their
non-deleted paths occur in the checkpoint diff and changed before the merged
head.

Historical line anchors match checkpoint content directly for 120 comments.
One outdated GitHub comment has an off-by-one `original_line`; its
`original_position`, terminal right-side hunk line, and adjacent checkpoint line
match. That exception is retained explicitly in the corpus.

Draft `line` and `start_line` are normalized **H/original** end and range-start
coordinates. They are not GitHub's current-head fields. Each root separately
retains `raw_current_line`, `raw_original_line`, `raw_current_start_line`,
`raw_original_start_line`, and `raw_start_side`. The checked-in values were
cross-checked against the complete cached REST response lists: 93 roots are
single-line/outdated, 15 single-line/current, 11 multiline/outdated, and two
multiline/current. Source archival fails if a normalized H coordinate differs
from `original_line`/`original_start_line` or if any raw current/original
coordinate differs. In particular, an outdated multiline comment may correctly
have `start_line: null` in the current REST response while retaining a non-null
`original_start_line`; these values must never be substituted for one another.

The draft retains the current REST body and timestamps, but no comment-edit
history. For 119 of 121 comments, `updated_at` differs from `created_at`; those
records do not prove the exact wording that existed when H was reviewed. This
limitation is separate from line anchoring.

These checks establish reproducibility, not semantic truth. A changed path or
blob does not prove that the review issue was accepted or fixed.

The draft still records
`rename_detection: disabled_tree_only_provisional_count` for every case because
its historical size counts predate the release materializer. The current
tooling implements a deterministic Git diff policy—Myers, indent heuristic
disabled, external diff disabled, and 50% rename detection—but the draft has
not yet been rematerialized under that policy. Its existing manifests and size
bands therefore remain provisional.

## Release blockers

All of the following are required before this corpus can be scored or described
as paper-ready:

1. Generate and preserve the exact `archive-draft-sources` REST archive; the
   checked-in draft is not that raw source artifact.
2. Run semantic actionability adjudication for every included reviewer comment.
3. Compare checkpoint and final-head code against each comment's intent; a
   path/blob change alone is insufficient proof of a fix.
4. Retrieve complete selected review threads with authenticated GraphQL
   `isResolved`/`isOutdated` and review decision provenance.
5. Validate each current review body—especially the 119 updated records—against
   H, its complete thread, and fix evidence; disclose that prior edit text is
   unavailable.
6. Rematerialize every case with the implemented fixed 50% rename-aware diff
   policy, then freeze the resulting manifests, digests, and size buckets.
7. Bind code-change evidence to the curation packet's checkpoint-to-final diff
   digest and exact modified/renamed/deleted path transition, including H/F
   paths and blob OIDs. Materialization must regenerate that evidence from the
   local H/F Git objects. Bind thread evidence to the canonical authenticated
   thread digest.
8. Recollect authenticated threads into the embedded raw GraphQL page archive,
   freeze the endpoint/query/variables/response cursor chain, and bind each
   normalized selected thread to its raw node/page.
9. Obtain at least two independent, digest-checked annotator records for every
   inclusion and exclusion; accepted records use `verdict: accept`, excluded
   records use `verdict: exclude`, and declared annotator identities must match.
   Publish disagreements, adjudication, and policy.
10. Pass `release-selection --paper-ready` with the exact REST source archive,
   authenticated thread evidence, and curation packet, then pass
   `validate --paper-ready`.
11. Do not report precision, recall, F1, FP, or FN from this provisional corpus.

## Curation workflow

Run `verify-current-comments` before curation to make a low-request, exact
drift check of all 121 current REST roots and their complete REST reply sets.
The generated artifact is deliberately not checked in and remains provisional:
live mode records a current REST observation, while `--offline` binds only the
explicit cache. Neither supplies submitted-review state, GraphQL resolution,
or semantic labels, so the full release blockers above still apply.

Copy `curation-decisions.template.json` to a run-specific decision file. Fill
the null fields using the defined LLM-judge protocol, retain judge evidence,
then require independent human annotation and an identified adjudicator to
resolve uncertain or disputed items. Judge evidence cannot replace the two
digest-checked human accept records required for each paper-ready issue. Only a
separate release step may set `include` to `true`; the template intentionally
contains no inferred labels.

Release selection must explicitly resolve the existing semantic, checkpoint,
review-acceptance, and final-head decisions plus every added gate:

- `same_root_cause_fix`
- `thread_complete`
- `thread_disposition`
- `summary`
- `root_cause`
- `failure_mode`
- `required_change`
- `category`
- `severity`
- `atomic`
- `fix_commit_sha`
- `fix_evidence`
- `exclusion_reason`
- `adjudication`

A null value is never an implicit pass. In particular, `fix_evidence` and
`adjudication` are deliberately `null` in this template and must not be
invented. `include` must remain `false` until the release selector validates
the applicable fields against collected evidence and the final adjudication.

Verify the frozen draft from this directory with:

```bash
sha256sum --check corpus-draft.sha256
```

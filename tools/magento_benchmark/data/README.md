# Magento 2 benchmark corpus data

This directory contains a **provisional, unscored** 50-PR corpus draft. It is
not a released golden benchmark and must not be used to report precision,
recall, F1, false-positive, or false-negative results.

The `provisional_unscored` decision-template status and
`provisional_unscored_anchor_validated` corpus status mean that mechanical
replay checks passed while semantic labels and scoring eligibility remain
unset.

Every case is an immutable historical review checkpoint from a pull request
merged into `magento/magento2:2.4-develop`. The included review comments remain
excluded from scoring until their null adjudication fields are completed and
the release blockers below are closed.

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

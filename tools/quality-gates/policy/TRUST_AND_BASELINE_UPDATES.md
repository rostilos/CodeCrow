# P0-07 trust and baseline update procedure

The quality gate is only authoritative when the workflow and its input
contracts are owned outside the pull request being evaluated. A hash literal in
the same pull request is not a trust anchor.

## Required repository controls

Before this gate is made required, a repository administrator must:

1. Merge the initial P0-07 implementation through an explicitly recorded
   bootstrap review. The bootstrap cannot claim that its newly introduced files
   were already protected by the default branch.
2. Configure a repository ruleset for every protected branch that requires the
   default-branch P0-07 workflow, requires CODEOWNER review, dismisses stale
   approvals, prevents force pushes, and prevents required-check bypass except
   by the named emergency administrators.
3. Set the repository Actions variable `P007_TRUST_BUNDLE_SHA256` to the exact
   SHA-256 of `tools/quality-gates/policy/trust-bundle-v1.json` from the
   protected default branch.
   Pull requests must not be allowed to write repository Actions variables.
4. Configure the required workflow from the protected default-branch revision
   (or an organization-owned required reusable workflow). A pull-request copy
   of a workflow with the same display name is not an acceptable substitute.
5. Retain the ruleset export, variable audit event, bootstrap review, and
   required-workflow identity in the P0-07 evidence record.

The workflow verifies the externally supplied digest before coverage starts and
again immediately before final evidence revalidation/checksums. The bundle
binds the gate implementation, policies, schemas, coverage configuration,
workflow and CODEOWNERS, offline runner/lock/settings, every Java reactor POM,
the Java coverage/legacy wrappers, and the exact real-mutation selector.
Missing variables, a stale bundle, an omitted required path, or any bound-file
drift is a hard failure. The pre-test source inventory's semantic digest and raw
artifact digest are protected step outputs; final evaluation must match both,
re-read every report/policy/baseline/change input, and record their digests in
the gate-result provenance receipt.

Immediately after checkout, and before setup actions, dependency resolution,
POM evaluation, or any candidate-controlled script, the protected workflow
performs a bootstrap check with trusted runner binaries. The external
Actions-variable digest is injected directly into that one step; it is never
copied into the job environment or through `GITHUB_ENV`. Isolated system
Python (`/usr/bin/python3 -I -S`) first matches the raw bundle to that digest,
parses only its narrow sorted manifest shape, and opens every listed path with
no-follow traversal before checking its digest. Only then may the workflow
import candidate quality-gate code and invoke the semantic bundle verifier.
The final evidence step independently injects the external digest again,
matches it to the bootstrap step output, and repeats the isolated no-follow
check before semantic verification and evaluation. This ordering prevents a
pull request from weakening the verifier that is supposed to authenticate it.

## Ordinary pull requests

Ordinary application changes may not edit the trust bundle or any bound file.
They resolve the complete P0-01 dirty state, pin the complete source inventory
before tests, carry that epoch through every normalized report, and independently
re-resolve source and Git inventories before and after final evaluation.

## Deliberate contract or baseline updates

A quality-contract update is a privileged, two-person change:

1. Open a dedicated pull request containing only the gate/policy/schema/test
   change and its evidence. Do not combine it with a coverage regression or an
   application behavior change.
2. Record RED evidence, focused GREEN evidence, full offline evidence, exact
   line/branch coverage, mutation results, and the independent review. A
   baseline update additionally records the pre-test source inventory,
   normalized module and repository reports, source snapshot, and exact
   comparison-base dirty replay.
   The coverage baseline represents the reviewed pre-runtime source epoch, but
   its Git comparison base remains the exact P0-01 commit required by the plan.
   Any later source-byte change relative to that file map must therefore have
   full-file line/branch coverage, including changes that a fixed P0-01 diff
   cannot express (reverts or second edits to post-P0-01 files).
3. Regenerate `trust-bundle-v1.json` from the reviewed final bytes, then compute
   its raw digest:

   ```bash
   PYTHONPATH=tools/quality-gates python -m quality_gates \
     capture-trust-bundle --repository-root . \
     --output tools/quality-gates/policy/trust-bundle-v1.json
   sha256sum tools/quality-gates/policy/trust-bundle-v1.json
   ```

   Review the path list and every digest; do not accept a generator-only diff.
4. A CODEOWNER other than the implementer approves the bundle and evidence.
5. An authorized administrator uses the documented ruleset bypass for this one
   merge, immediately updates `P007_TRUST_BUNDLE_SHA256` to the merged default-
   branch bundle, and records both audit events.
6. Run the required workflow again from the untouched merged revision. If any
   byte changes after qualification, discard the evidence and repeat.

Never recapture the baseline to make a regressing change pass. Expired
exclusions are removed or renewed through the same independent-review process.
The protected P0-03 runner and dependency lock are never rewritten as part of a
P0-07 baseline update.

## Emergency rollback

Rollback uses a reviewed revert of the complete P0-07 contract, preserves the
evidence bundle, and restores the preceding externally pinned trust-bundle
digest. It must not delete immutable source/test evidence or alter the protected
user change in `deployment/build/production-build.sh`.

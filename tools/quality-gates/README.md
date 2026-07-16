# CodeCrow quality gates

This directory contains the P0-07 changed-path coverage and deliberate-mutation
gates. The tooling is additive: it does not alter application runtime behavior.
It consumes exact JaCoCo and coverage.py evidence, the P0-01 comparison-base
attestation, and the frozen pre-runtime coverage baseline.

Run all commands from the `codecrow-public` repository root. Application tests
must use the pinned offline runner. Never run more than one Maven reactor at a
time, and do not regenerate a baseline while application source is changing.

## Contracts and policies

Normalized reports use `schemaVersion: 1` and identify their adapter, tool
version, language, module, exact pre-test `sourceInventorySha256`, source files,
executable and covered lines, branch counters, and exact integer totals. Every
module and repository aggregate must carry the same inventory epoch. The
semantic validators recompute every total and reject duplicate, ambiguous,
escaping, missing, stale, mixed-epoch, or branch-disabled data. The JSON schemas
in `schema/` document the wire shapes; the Python
validators remain authoritative for relationships JSON Schema cannot express,
including sorted source lines, counter reconciliation, disjoint module paths,
and aggregate equality.

The checked-in policies are:

- `policy/comparison-base-v1.json`: byte-exact extraction of the P0-01
  comparison commit, manifest identity, and exact P0-01 dirty-state replay.
- `policy/source-inventory-policy-v1.json`: independent ownership and coverage
  disposition for every Java/Python runtime source; unlisted source is fatal.
- `policy/correctness-policy-v1.json`: default-critical changed-path
  classification with only reviewed, narrow test-material exceptions.
- `policy/java-modules-v1.json`: the 18 exact aggregate group/source-root
  mappings.
- `policy/coverage-domains-v1.json`: the exact 23 Java/Python baseline domains
  (18 Java modules, three Python modules, and two repository aggregates).
- `policy/coverage-baseline-v1.json`: source-bound file shapes and fresh
  line/branch counters for each module and both repository aggregates.
- `policy/source-snapshot-v1.json`: SHA-256 identity of every application
  source represented by the baseline.
- `policy/exclusions-v1.json`: reviewed, expiring exceptions; initially empty.
- `policy/mutation-profile-v1.json`: deterministic state, identity/evidence,
  budget, fencing, and reconciliation mutants against real gate predicates.

The source inventory is resolved before any coverage-producing test. Its
canonical digest binds policy identity, path, language, module, disposition,
and source bytes. Normalizers receive that pinned value; final evaluation
rescans the inventory and resolves the complete Git worktree both before and
after counter evaluation. This catches source drift, protected dirty-state
drift, and changed correctness configuration after the first resolution.
Repository-side protection and the privileged bundle/baseline update procedure
are defined in `policy/TRUST_AND_BASELINE_UPDATES.md`. Immediately after
checkout and before setup, dependency, POM, or candidate-script execution, the
workflow uses isolated system Python and no-follow reads to verify
`policy/trust-bundle-v1.json` against a step-local direct reference to the
externally administered `P007_TRUST_BUNDLE_SHA256` Actions variable. It repeats
that direct external binding and isolated verification immediately before final
evidence qualification; the protected value is never carried through the job
environment or `GITHUB_ENV`. A pull-request-controlled workflow, bundle, or
digest literal is not treated as a trust anchor.

Frontend coverage is intentionally deferred to P4-09. A frontend adapter must
join the same normalized contract before a frontend correctness path can pass.

## Deterministic local checks

The locked Python environment and offline runner are P0-03 artifacts. Verify
their pinned identities before use:

```bash
sha256sum tools/offline-harness/bin/run-offline.sh \
  tools/offline-harness/requirements/ci-test.lock
# runner: 839d8945913bc385d772b3da3bb9dacc0ff871a4195159ea1ad8a374362ee86f
# lock:   d3629cfc00ed139614507929681d67199dc0c66f980f460d939543e3373b84c7
```

For release acceptance, obtain `P007_TRUST_BUNDLE_SHA256` from the protected
repository Actions variable (never from the candidate branch), perform the
protected workflow's manifest bootstrap, and then run the semantic verifier:

```bash
test -n "$P007_TRUST_BUNDLE_SHA256"
PYTHONPATH=tools/quality-gates "$PYTHON" -m quality_gates \
  verify-trust-bundle \
  --bundle tools/quality-gates/policy/trust-bundle-v1.json \
  --bundle-sha256 "$P007_TRUST_BUNDLE_SHA256" \
  --repository-root .
```

The default-branch required workflow contains the canonical immediate
post-checkout bootstrap. It hashes every bundle entry with trusted runner tools
before any candidate-controlled execution or candidate quality-gate import;
the semantic command alone is not a substitute for that trust boundary.

Run the quality implementation at exact line and branch coverage. The wrapper
behavior tests are kept separate because they require host support for
unprivileged Bubblewrap namespaces; the exact derived-wrapper transform test is
always runnable.

```bash
PYTHON=.llm-handoff-artifacts/p0-03/locked-python311/bin/python
tools/offline-harness/bin/run-offline.sh "$PYTHON" -m pytest \
  tools/quality-gates/tests -q \
  --ignore=tools/quality-gates/tests/test_java_coverage_offline_wrapper.py \
  --cov --cov-branch \
  --cov-config=tools/quality-gates/config/quality-gates.coveragerc \
  --cov-fail-under=100 --cov-report=term-missing
"$PYTHON" -m pytest -q \
  tools/quality-gates/tests/test_java_coverage_offline_wrapper.py::\
test_derived_wrapper_is_exact_audited_transform_of_p003
```

The Java coverage report is produced by the profile-only aggregate module. It
must use the frozen P0-07 Maven cache and the audited derived wrapper. Obtain an
explicit serialized-reactor lease before running this command:

```bash
export CODECROW_MAVEN_REPOSITORY="$PWD/.llm-handoff-artifacts/p0-07/dependency-cache/maven"
export CODECROW_P007_CACHE_RECEIPT_SHA256='<sha256 from the cache-freeze step>'
tools/quality-gates/bin/run-java-coverage-offline.sh \
  mvn -f java-ecosystem/pom.xml \
  -s tools/offline-harness/maven/settings-ci.xml \
  -o -B --no-transfer-progress \
  -Pquality-coverage,p007-prebuild-without-integration-execution \
  -pl quality/coverage-aggregate -am clean verify
for lane in queue pipeline web; do
  tools/quality-gates/bin/run-java-legacy-it-guarded.sh "$lane"
done
tools/quality-gates/bin/run-java-coverage-offline.sh \
  mvn -f java-ecosystem/pom.xml \
  -s tools/offline-harness/maven/settings-ci.xml \
  -o -B --no-transfer-progress \
  -Pquality-coverage,p007-aggregate-only \
  -pl quality/coverage-aggregate -am verify
```

The prebuild profile compiles the complete reactor and runs its local unit
coverage without executing legacy integration classes. The workflow next runs
the exact 11-class/65-test local-double selector inventory under
`p007-integration-only`, with `-am` to bind current-checkout upstream modules and
`-Dfailsafe.failIfNoSpecifiedTests=false` only for dependency modules that do not
own one of those exact selectors, and validates its fresh Failsafe reports. The guarded wrapper then
activates `p007-integration-only` plus exactly one of
`p007-guarded-{queue,pipeline,web}-it`; it provisions the reviewed task-owned
service capability, executes the exact guarded-only selector census, and removes
the container before returning. Its sandbox keeps the repository read-only except
for the fixed current-reactor module targets and generated reactor-root target
that Maven/Failsafe requires; every writable target must be a prebuilt real
directory with no symlink. The aggregate-only invocation then merges the
unit, local-double, and guarded execution data without rerunning either test
engine. Do not replace these profiles with ad-hoc `skipITs` command-line
properties.

Resolve and retain the pre-test inventory before running coverage:

```bash
QUALITY=(env PYTHONPATH=tools/quality-gates "$PYTHON" -m quality_gates)
"${QUALITY[@]}" resolve-source-inventory \
  --policy tools/quality-gates/policy/source-inventory-policy-v1.json \
  --repository-root . \
  --output .llm-handoff-artifacts/p0-07/source/pre-test-inventory.json
SOURCE_INVENTORY_SHA=$("$PYTHON" -c '
import json
print(json.load(open(".llm-handoff-artifacts/p0-07/source/pre-test-inventory.json"))["inventorySha256"])
')
SOURCE_INVENTORY_ARTIFACT_SHA=$(sha256sum \
  .llm-handoff-artifacts/p0-07/source/pre-test-inventory.json | cut -d' ' -f1)
```

Normalize the aggregate after the tests. This command fails unless all 18 groups match policy,
all source paths resolve exactly once, group totals reconcile with the root,
and repository line and branch counters are present. JaCoCo's omitted BRANCH
counter is interpreted as 0/0 only when every line and every nested branch
counter independently proves zero branches.

```bash
PYTHONPATH=tools/quality-gates "$PYTHON" -m quality_gates \
  normalize-jacoco-aggregate \
  --input java-ecosystem/quality/coverage-aggregate/target/site/jacoco-aggregate/jacoco.xml \
  --module-policy tools/quality-gates/policy/java-modules-v1.json \
  --repository-root . --tool-version 0.8.11 \
  --source-inventory-sha256 "$SOURCE_INVENTORY_SHA" \
  --aggregate-output .llm-handoff-artifacts/p0-07/coverage/java/aggregate.json \
  --module-output-root .llm-handoff-artifacts/p0-07/coverage/java/modules
```

The equivalent `normalize-coveragepy` commands require the same pinned digest;
`aggregate` derives and checks the digest from its inputs. They are shown in
`.github/workflows/offline-tests.yml`. That workflow runs unit and integration
suites with `--cov-branch --cov-append`, validates zero-live-call ledgers,
normalizes both Python applications and the quality-gate implementation,
resolves the full Git base without a fallback, and evaluates all module and
repository reports.

## Resolve and evaluate changes

CI and local acceptance always resolve the complete worktree from the attested
P0-01 commit. There is no committed-only or unattested baseline mode in the
release gate.

```bash
QUALITY=(env PYTHONPATH=tools/quality-gates "$PYTHON" -m quality_gates)
"${QUALITY[@]}" resolve-changes \
  --repository-root . \
  --base-attestation tools/quality-gates/policy/comparison-base-v1.json \
  --base-attestation-sha256 58b54d329ca06db021eb26e2b32a58a20ab6794e39dc6da91224a13e33802666 \
  --baseline-manifest-sha256 be9893de0ad6dc3de087aac21aac11f79b1c8f8962e7184782c53016bacd3c9c \
  --include-worktree \
  --correctness-policy tools/quality-gates/policy/correctness-policy-v1.json \
  --output .llm-handoff-artifacts/p0-07/base/changes.json
```

Pass every normalized module report and both authoritative repository
aggregates to `evaluate`, together with `policy/coverage-baseline-v1.json` and
`policy/exclusions-v1.json`. Also pass the source/correctness policies and the
same comparison-base attestation arguments used by `resolve-changes`. The raw
pre-test inventory must be supplied with `--pinned-source-inventory` and its
protected raw digest with
`--pinned-source-inventory-artifact-sha256`; matching only the semantic epoch is
insufficient. The workflow contains the canonical complete invocation. The gate
requires 100% of executable changed lines and branches, rejects a missing
correctness file/report, prevents baseline ratio regression with exact cross
multiplication, and requires any new domain to be 100%. Its result records the
semantic source epoch, canonical full-change inventory digest, and raw digests
of every report, baseline, exclusion registry, policy, attestation, and pinned
inventory input. Every bound input is re-read before the atomic result write.

## Exclusion approval and receipts

An exclusion is exceptional, narrow, and temporary. Add one entry only after
an independent reviewer approves all of the following:

1. A repository-relative file/glob that cannot select an entire broad tree.
2. A concrete reason, owner, different reviewer, and ISO expiry date.
3. One compensating integration-test selector and a base-approved
   `executionPolicy`: exact repository runner and runtime-wrapper paths/SHA-256,
   plus an argv template containing one `{runtime}` and ending in the sole
   `{selector}`.
4. One stable receipt-manifest path under `.llm-handoff-artifacts/`. Current
   commit, change-inventory digest, source bytes, absolute runtime identity,
   expanded argv, exact JUnit selector result, and reconciled zero-live-call
   ledger live only in the freshly qualified manifest. They are deliberately
   not embedded in the checked-in registry, which would make the registry
   depend on its own future commit and artifact hash.

Run every distinct approved selector once, then qualify every source-bound
manifest with `capture-exclusion-receipts`; the workflow contains the canonical
invocation. Pass one repeatable `--selector-evidence SELECTOR JUNIT LEDGER`
tuple for each distinct registry selector so each contract remains bound to its
own report and zero-live-call ledger. The legacy `--junit`/`--ledger` pair is
valid only when the registry contains exactly one selector. The evaluator
records every actual manifest SHA-256 in gate provenance and re-reads those
manifests before its atomic result write.

The evaluator rejects expired entries, same-person approval, overlapping
matches, missing receipts, stale commits/inventories/sources, malformed hashes,
nonpassing/skipped tests, selector/class mismatches, runtime/runner/argv drift,
live calls, XML entities/namespaces, and receipt paths outside the evidence
root. Never mark generated or excluded lines as covered. Configuration,
mappings, exceptions, retries, and fallback logic are correctness paths, not
trivial wiring.

## Deliberate mutation gate

Run the five deterministic mutants through the pinned isolation runner:

```bash
PYTHONPATH=tools/quality-gates "$PYTHON" -m quality_gates run-mutations \
  --repository-root . \
  --profile tools/quality-gates/policy/mutation-profile-v1.json \
  --artifact-root .llm-handoff-artifacts/p0-07/mutation-local \
  --python-runtime "$PYTHON" \
  --offline-runner tools/offline-harness/bin/run-offline.sh \
  --offline-runner-sha256 839d8945913bc385d772b3da3bb9dacc0ff871a4195159ea1ad8a374362ee86f \
  --output .llm-handoff-artifacts/p0-07/mutation-local/cli-result.json
```

Each mutation changes one real receipt state, receipt-artifact identity, exact-ratio,
comparison-base fence, or JaCoCo reconciliation predicate and runs alone in an
allowlisted disposable snapshot. `KILLED` means exactly one selected expected
assertion failed and the JUnit counters reconcile.
Exit zero is `SURVIVED`; timeout, malformed/missing receipt, unrelated error, or
isolation failure is not a kill. The runner removes stale work/results before a
run and records bounded logs and immutable before/after identities.

## Baseline reproduction and updates

The tracked baseline is valid only when its snapshot digest matches the tracked
source snapshot and the snapshot verifies against the checkout:

```bash
SNAPSHOT=tools/quality-gates/policy/source-snapshot-v1.json
SNAPSHOT_SHA=$(sha256sum "$SNAPSHOT" | cut -d' ' -f1)
PYTHONPATH=tools/quality-gates "$PYTHON" -m quality_gates \
  verify-source-snapshot --snapshot "$SNAPSHOT" \
  --snapshot-sha256 "$SNAPSHOT_SHA" --repository-root . \
  --source-inventory-policy \
    tools/quality-gates/policy/source-inventory-policy-v1.json
```

To reproduce a baseline, first resolve the complete source inventory and run the
complete isolated Java and Python suites on that unchanged inventory epoch.
Pass all 18 Java module reports, all three Python module reports (both
applications and the quality-gate implementation), and both repository
aggregates to `capture-source-snapshot` with `--source-inventory-policy`. Then
pass the same complete report set to `capture-baseline`, with comparison base
`89287e1fce55dc9bffeca2b92ce660d8791ae6ac`, the captured snapshot SHA-256,
`policy/coverage-domains-v1.json`, `--repository-root .`, and
`--source-inventory-policy`. The baseline records every required file's source
SHA, executable lines, branch shape, and module domain. The file map represents
the later reviewed pre-runtime epoch while Git changes remain anchored to the
plan-mandated P0-01 commit. A current source whose bytes differ from that map
must therefore have full-file line and branch coverage, even if a revert or a
second edit to a post-P0-01 file produces no useful P0-01 hunk. Regeneration
must produce byte-identical files unless a separately reviewed baseline update
is intentional. Never lower or recapture the baseline in the same change that
regresses coverage.

## Fail-closed behavior and recovery

Exit `2` means malformed or untrusted input. Exit `1` means valid evidence that
does not satisfy coverage or mutation policy. Missing/full-history Git bases,
shallow clones, bad attestations, unsafe paths, symlink outputs, counter
forgery, duplicate domains, incomplete branch data, mixed/stale inventory
epochs, pinned-artifact/raw-input drift, post-resolution worktree drift,
expired exclusions,
unreconciled aggregates, isolation failures, and surviving mutants all block.
There is no `HEAD^`, branch-name, aggregate-threshold, or live-network fallback.

For rollback, remove the `quality-coverage` profile activation, aggregate
module, workflow gate step, and this additive `tools/quality-gates` tree as one
reviewed change. Remove its generated `.llm-handoff-artifacts/p0-07` evidence
only after preserving the review record. Then run the unchanged P0-03 offline
harness regression and the ordinary application build to prove runtime outputs
are unchanged. Do not edit the frozen P0-03 runner/cache/lock, application
runtime source, or protected user files as part of rollback.

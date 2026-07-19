# CodeCrow offline evaluation

This directory is the P0-05 evaluation boundary. It scores recorded PR-level
artifacts, registers mutually disjoint corpus purposes, commits protected split
components without copying their contents, and records every protected access
decision in a tamper-evident ledger. It is additive tooling; it does not change
review selection, prompts, pruning, publication, or application runtime.

The checked-in corpus inventory is intentionally honest. The visible Goodwine
issue export is diagnostic-only, the public Martian offline corpus is pinned as
a 50-PR/136-label calibration snapshot, and no independently sealed internal
reserve is present in this workspace. Do not replace any of those states with
synthetic or renamed data. `P0-05` cannot be approved until an independent custodian supplies opaque
commitments and a disjointness attestation for a primary P5-06 set and a
post-P5-08 confirmation reserve.

## Contracts

- `policy/scoring-policy-v1.json` freezes PR-level matching, duplicate,
  unsupported, clean-control, confidence-interval, severity, cost, and latency
  semantics. Every result includes the exact raw policy SHA-256.
- `policy/corpus-inventory-v1.json` records what is actually available and the
  limits on each source. It contains no protected identity, label, or outcome.
- `schema/` defines the version-1 evaluation, registry, ledger, oracle, and
  corpus wire shapes. Python validators additionally enforce relationships that
  JSON Schema cannot express.
- `policy/LABELING_AND_ACCESS_PROTOCOL.md` is the custodian and unblinding
  procedure. Protected access is never granted to planning, pruning,
  implementation, or policy-selection roles, even after a gate opens. A
  self-hashed receipt is insufficient: its digest must arrive through the
  protected gate context supplied by the custodian.

## Score a recorded bundle

Use the P0-03 locked runtime and network-deny runner from the repository root:

```bash
PYTHON=.llm-handoff-artifacts/p0-03/locked-python311/bin/python
tools/offline-harness/bin/run-offline.sh "$PYTHON" \
  tools/evaluation/bin/codecrow-evaluation.py \
  score --input /absolute/path/evaluation-input.json \
  --output .llm-handoff-artifacts/p0-05/results/result.json
```

Set `PYTHONPATH=tools/evaluation` when invoking the package outside pytest. The
scorer rejects duplicate IDs, dangling or cross-label duplicate links,
unsupported matched claims as true positives, hidden false negatives in
partial/abstained runs, impossible coverage counts, missing resource measures,
and an input policy identifier that differs from the loaded policy.

To compare the two selectable engines on the same frozen case truth, record one
bundle with `provenance.reviewApproach=CLASSIC` and one with
`provenance.reviewApproach=AGENTIC`, then run:

```bash
PYTHONPATH=tools/evaluation "$PYTHON" -m codecrow_evaluation compare-approaches \
  --classic /absolute/path/classic-input.json \
  --agentic /absolute/path/agentic-input.json \
  --output /absolute/path/paired-result.json
```

The command rejects different case IDs, labels, coverage inventories, corpus,
model, rules, index revision, or source revisions. It reports precision, recall,
F1, HIGH/CRITICAL precision, TP/FP/FN, cost, latency, coverage, true-positive
retention, and per-PR deltas. Provider-reported cost deltas are `null` unless
both runs report provider cost for every case.

This command scores already-recorded bundles; it does not invoke either review
engine. The bundle producer must run both approaches against the same immutable
PR manifests before using the comparison for a shadow-rollout decision.

Version 1 can measure the HIGH/CRITICAL severity slice, but not
security-category precision because frozen labels and predictions have no defect
category. It also cannot score hypothesis dispositions, prior-finding
reconciliation, tool use, cleanup failures, or per-finding anchor/evidence
completeness. Those rollout gates require category-labelled truth and bound
execution/evidence telemetry; they must not be inferred from severity or missing
fields.

Per PR and in aggregate it reports TP/FP/FN, precision, recall, F1,
HIGH/CRITICAL precision, duplicates,
unsupported output, clean-control outcomes, coverage represented/total,
estimated and provider-reported cost, latency, analysis state, and severity
calibration. Aggregate precision, recall, and HIGH/CRITICAL precision carry
deterministic 95% Wilson intervals. A zero denominator is `null`, never a
fabricated zero.
When a case includes the optional `context` adjudication, the same result also
reports context precision/recall, duplicate retrieval, snapshot and digest
integrity, exact-base-index availability, and explicit context-gap frequencies.
This separates “the model missed the defect” from “the analysis never supplied
the dependency needed to see it.” Missing context adjudication is reported as
unmeasured rather than treated as a passing zero-error retrieval.
The input must also bind the P0-01 manifest, both source revisions and dirty
state, registry/corpus/oracle/telemetry/environment digests, exact
model/prompt/rule/index versions, seed, and argv. Protected inputs additionally
require the access-grant event hash and raw ledger-head artifact digest.

## Corpus adapters

The public Martian source is pinned at commit
`dfc6cb427b5d0d7492a8d875ee9447744b7de3d1`. The checked-in snapshot
descriptor binds all five golden files (50 PRs, 136 human-curated labels), the
MIT license, offline README, benchmark-data export, and PR-label export. Import
an exact checkout without network or model use:

```bash
tools/offline-harness/bin/run-offline.sh "$PYTHON" \
  tools/evaluation/bin/codecrow-evaluation.py import-martian \
  --descriptor tools/evaluation/policy/martian-offline-snapshot-v1.json \
  --snapshot-root .llm-handoff-artifacts/p0-05/martian/code-review-benchmark \
  --output .llm-handoff-artifacts/p0-05/martian/catalog-v1.json
```

The importer verifies every bound byte and derives stable label IDs from case,
comment position/text, severity, and label version. The disclosed CodeCrow run
configuration records the benchmark-specific index scope and requires each
actual run to bind its P0-04 prompt/model/embedding/judge attribution.
`build_martian_manifest` accepts only an existing local content-addressed export
and that disclosed configuration. Public benchmark data can be development,
calibration, or diagnostic; it cannot be relabeled as a sealed acceptance
split. The existing review harness uses live GitHub, model, embedding, and judge
services, so producing new predictions is outside the zero-live-token test path.

`build_goodwine_manifest` accepts the visible CSV and always emits
`purpose=diagnostic` plus `supportsFalseNegatives=false`. It cannot support a
recall claim because it contains only already reported issues.

Neither adapter authorizes a customer-performance claim. Live shadow evidence
is required by a later phase.

Executable oracle specifications hash their complete argv and reject every
file-valued argv entry unless that file is declared with a digest in `artifacts`.
Both direct paths and option values such as `--config=/path` are checked when a
specification is registered and again immediately before execution, including
files created after registration. Execution also rechecks the interpreter or
binary, each artifact, and the P0-03 runner before launch; the result records all
those identities. A version string without matching bytes is not an oracle.

## Protected commitments and registry

The custodian keeps identities, labels, and outcomes outside the implementation
workspace and runs:

```bash
PYTHONPATH=tools/evaluation "$PYTHON" -m codecrow_evaluation commit-bundle \
  --split-id primary-v1 \
  --identities /custodian-only/identities.json \
  --labels /custodian-only/labels.json \
  --outcomes /custodian-only/outcomes.json \
  --output /safe/opaque-primary-commitments.json
```

The output contains only three SHA-256 commitments and the opaque split ID.
Create the registry under custodian control, then validate it and derive the
only view allowed to behavior-affecting code:

```bash
PYTHONPATH=tools/evaluation "$PYTHON" -m codecrow_evaluation validate-registry \
  --input /custodian-only/split-registry.json \
  --policy-context-output /safe/public-policy-context.json
```

The public policy context excludes every protected split ID, count, gate,
commitment, and custody field. Changing those protected values therefore cannot
change the planning/policy input.

## Reproducibility and P0-01 binding

An evaluation manifest must bind the two repository revisions and dirty state,
the evaluation input and result digests, split-registry version and digest,
scoring-policy digest, corpus/configuration digests, label and oracle versions,
P0-04 execution/configuration/model/prompt/rule/index attribution, deterministic
seed where a later method uses one, command, locked runtime, offline-runner
digest, environment fingerprint, and zero-live-call ledgers.

Results are canonical UTF-8 JSON with sorted keys and a final newline. Case and
label ordering does not change a score. Do not put protected bundle locations,
contents, or credentials in the P0-01 manifest; bind their opaque commitments
and the independently reviewed access ledger instead.

## Focused tests

```bash
PYTHON=.llm-handoff-artifacts/p0-03/locked-python311/bin/python
tools/offline-harness/bin/run-offline.sh "$PYTHON" -m pytest \
  tools/evaluation/tests -q
```

This command consumes no provider credentials, model calls, embedding calls,
VCS calls, or metered tokens.

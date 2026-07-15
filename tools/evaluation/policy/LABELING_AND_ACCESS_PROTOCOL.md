# P0-05 labeling, custody, and unblinding protocol

## Roles and separation

The program owner/implementer, evaluation custodian, and independent label
reviewer are three different people or independently administered identities.
The custodian alone holds protected case identities, labels, and outcomes. The
reviewer can approve labels and a registered unblinding event but cannot alter
implementation or scoring policy during that acceptance run.

Planning, pruning, implementation, and policy-selection actors receive only the
public policy context produced by `SplitRegistry.policy_context()`. They never
receive protected split IDs, counts, gates, commitments, labels, outcomes, or
score summaries. This prohibition remains after unblinding; protected access is
limited to the scorer and independent reviewer.

## Dataset construction and labeling

1. Establish mutually disjoint development, calibration, primary P5-06
   held-out, and post-P5-08 confirmation-reserve membership before acceptance
   work. The independent custodian and reviewer sign one membership digest that
   covers every opaque split ID.
2. Each protected split must independently attest positive defects, hard
   negatives, clean controls, large PRs, multiple implementation languages,
   collision cases, renames, and cross-file behavior. Feature names and counts
   may be attested; case identities do not enter the implementation workspace.
3. Give every label an immutable label ID and label-version identifier. Prefer
   a pinned executable or static oracle. A subjective label requires at least
   two distinct labelers and a separate adjudicator, with a concise rationale.
4. Store identities, labels, and outcomes as three separate canonical bundles.
   The custodian publishes only their SHA-256 commitments, custody identities,
   registered gate, sealed timestamp, feature attestation, and case count in the
   protected registry.
5. Public/disclosed corpora remain development, calibration, or diagnostic.
   Visibility can never be reversed by renaming a split.

## Gate receipt and access ledger

A gate receipt has `schemaVersion`, opaque `splitId`, exact `gate`,
`decision=unblind`, registered `custodian`, registered independent `approvedBy`,
`approvedAt`, and `expiresAt`. Its `receiptSha256` is SHA-256 over canonical JSON
of those fields excluding `receiptSha256` itself (UTF-8, sorted keys, compact
separators).

The custodian publishes the authorized receipt digest through the registered,
protected gate context. It must not be read from an implementation-controlled
registry, candidate branch, environment file, or receipt itself.
`AccessController.authorize()` requires that external trusted-digest input, then
verifies receipt contents, exact split/gate/custody binding, approval identity,
active time window, role, and requested data classes before returning an opaque
grant. A correctly self-hashed but externally untrusted receipt is denied.

Every grant and denial is appended under an exclusive file lock as one
canonical JSON line containing a sequence number, prior-event hash, and current
event hash. An atomically replaced, fsynced head file binds event count and final
hash. The ledger never contains bundle contents or commitments. Restart
revalidates the entire chain and head, reapplies every recorded demotion, and
refuses another append after alteration, prefix truncation, malformed JSON,
sequence drift, or hash drift. The P0-01 run manifest additionally binds the raw
ledger and head artifact digests from custodian-controlled storage.

The scorer runs with the P0-03 offline runner and writes its result into a
custodian-controlled result area. Only the aggregate evidence explicitly
authorized by the acceptance gate may leave that area.

## No tuning and reserve retirement

Development and policy changes may use only development/calibration data and
diagnostic sets already designated as visible. They must not use primary or
confirmation identities, labels, per-case outcomes, aggregate outcomes, or
failure localization before the registered gate.

If unblinded acceptance results cause any behavior-affecting change—including a
prompt, rule, model policy, pruning threshold, code path, label interpretation,
or legacy-retirement decision—the custodian records a demotion event. That set
becomes permanently diagnostic. A failed confirmation reserve therefore cannot
be rerun as acceptance after a fix; another untouched, preregistered reserve or
freshly acquired time-split sample is required.

Unblinding is irreversible. Deleting a ledger or changing a registry version
does not reseal knowledge already disclosed.

## Publication boundary

P0-05 establishes repeatable offline measurement, not a customer claim.
Publication must name corpus visibility, configuration, revisions, policy and
oracle versions, confidence method, cost/latency basis, and all limitations.
Customer-performance or rollout claims require later live shadow and staged
release evidence.

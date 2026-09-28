# September 26 rerun: call volume and lost verifier outcomes

Read-only snapshot taken around 13:08 UTC. The benchmark was still running. The mcp-3 result artifact contained six completed PRs (Cal8087, Discourse1–5), while captures also included active Cal10600, Cal10967 and discovery for Cal7232. These are partial-run observations, not a final benchmark or paired F1 claim.

Raw capture archive: `/tmp/codecrow-sep26-rerun-captures.tar` (private). Decoded metadata/response index: `/tmp/codecrow-sep26-rerun-trace-index.json` (private). Runtime snapshot: `/tmp/codecrow-sep26-rerun-runtime.log` (private). Every indexed item contains exact `base` path; suffixes are `.request.body`, `.response.body` (gzip), `.start.json`, `.complete.json`.

829 complete model-call response records were readable, between 12:00:15 and 13:07:51 UTC. All captured calls selected `deepseek/deepseek-v4-flash-0731`. Captures now work. Current deployment is processing requests; no runtime configuration was changed.

## Primary defects observed in actual responses

1. **Work item protocol drops valid investigation answers.** 114 question IDs appeared in the `decisions` array, across 58 cases. `recordReviewDecisions` accepts an obligatory `decisions` field, optional `investigations`, and optional `findings`. The model repeatedly naturally addresses question IDs as decisions. The host silently ignores unknown candidate IDs and returns `status: ready`, empty `rejected`, and the same remaining question list. Cases then redo or abandon work. This is a contract/interface defect, not an evidence disagreement.

2. **A real newly identified issue was lost.** Discourse5 case3 turn22 correctly described the static header's `.panel` nested under `.row`: removing `float:right` leaves it outside the direct flex children. It sent the issue as `candidateId: NEW-FINDING-PANEL` in decisions, alongside a resolved question using its question ID. The host ignored both with empty rejections. Turn23 explicitly recognized the needed `investigations`/`findings` shape, but first reread an existing source range to obtain the changed line. The controller stopped for no novel source before the corrected record could be sent. The public result omitted this defect.

3. **Questions expand into independent repository audits after their answer is established.** Cal10600 case18 starts with only the question whether the login UI submits `backupCode`. Turn1 reads login.tsx, BackupCode.tsx, TwoFactor.tsx. Turn2 reasoning explicitly states: “The investigation question is answered: yes.” It then searches for new possible bugs in setup, storage, disable, migrations, and crypto through turn7. New source keeps the case alive even though assigned work is already answered. Telling the model to stop did not implement a finite work lifecycle.

4. **Some enormous outputs occur inside one provider generation.** Cal10600 case18 turn7 returned 100,247 output tokens, `finish_reason:error`, 347,325 content characters of repetitive “record & conclusion — FINAL” prose, zero tool calls. Cal10600 case7 turn4 returned 63,422 output tokens and 222,700 content characters of repetitive “FINAL/Emit/record” before finally emitting one recordReviewDecisions call. The requested final verdict is stable very early in both outputs. This cannot be addressed by trimming retrieved graph metadata or by a controller turn quota. A dedicated decision phase with a coherent structured contract is needed, and provider-generation failure must remain observable.

5. **Same contract investigated in many cases.** Cal10967 cases9/14/17/18/19/20 independently investigate destinationCalendar object-to-array migration/consumers: 65 calls, 1,365,081 input tokens, 49,174 output tokens so far. Across all its cases EventManager.ts was searched/read in 15 cases, handleNewBooking.ts in 13, CalendarManager.ts in 12. There were zero exact repeated same-case tool calls in this PR snapshot; suppressing exact repeats does not solve the duplicated semantic work. Cal10600's TwoFactorAuthAPI.ts was independently read whole in 10 cases, disable.ts in 9. Discourse4's topic_embed.rb was independently read whole in 10 cases.

6. **Literal grep invites failed regular-expression attempts.** Discourse5 case3 requests `\\.row`, `^\\.row`, `customLogoSettings|title`, and `contents.clearfix|contents` from a literal-only grep. These return empty complete results and lead to more searches. Explicit search mode/clear result semantics or a compatible search interface are preferable to interpreting these as evidence of absence.

## Workload

111 verifier cases began in this snapshot; 62 are question-only. They carry 129 investigations and 54 candidates. Of 701 verifier calls, 396 (56.5%) are question-only (5,594,981 input,486,016 output tokens), 305 candidate-bearing (4,455,841 input,491,259 output). Read tools: 487 readReviewFile, 388 grepReviewCode, 93 getReviewDiff, 48 queryCodeGraph, 3 getStructuralUnit, 3 getMinimalReviewContext, 2 getImpactRadius. recordReviewDecisions was invoked 168 times (166 parseable argument objects). Graph navigation cardinality is not the primary remaining call-volume cause.

Longest cases: Cal10967 case7 25calls/592,258input; Discourse5case3 23calls/531,987input; Cal10967case19 17calls/290,164input; Discourse4case25 16calls/338,562input; activeCal10967case17 16calls/476,383input.

## Exact evidence prefixes

- Discourse5case3turn22: `9f22d3e44ec1aecc4596195906b5dff9a9f2f425441d43b4f8b3f925f7e5dfd6/425a11a4408540ce956e8646fb4fd969/881f845f2a9843879af3d266a279941b-1`.
- Its turn23 request contains the silent-drop receipt and final reread: same directory `d29168b2206f458183aec2f24d7a782a-1`.
- Cal10600case18turn2, answered question then expanded investigation: `5de8e278d01d543693e7e13e8d9313e09f09d2757732baec8435cd2af452c14f/5d34d79949b04871ad14004c03bcc2f4/948fdca8ebdb4d2dbf21daf245ef9acb-1`.
- Cal10600case18turn7 runaway: same directory `0d5b32edc4e641988dfa809974117a37-1`.
- Cal10600case7turn4 runaway: same directory `06b47d6da9a54547960a8bf990da1ee4-1`.

Prefixes are relative to `/tmp/codecrow-sep26-rerun-captures/review-quality-captures/`.

## Implementation direction supported by this evidence

Use one explicit work-item identity and outcome contract; interpret known investigation IDs safely without silently losing them. Preserve potential new findings with an actionable repair receipt, especially source-complete findings in a malformed envelope. Replace new-source novelty as the continuation criterion with explicit unsettled causal premises and a settlement phase. Group/share answers to the same source contract across owners rather than rerunning source discovery per hunk. Keep complete relevant source behind evidence identities, and keep facts reusable without merging unrelated work into one unbounded conversation. Give final decisions a separate structured emission path so source navigation and verdict formatting do not compete in the same native-tool continuation.

## Implemented local navigation and offline verification

`findReviewFiles` now provides a source-free path lookup through the same host-bound,
descriptor-backed snapshot traversal as local reads. Filename globs match at any
depth, and repository-relative globs support `**`. Proposed files honor the overlay
and deletions. Missing or unsafe overlay files are reported as unavailable paths;
target source is never substituted for an unavailable changed file. Symlinks,
parent traversal and `.git` metadata remain inaccessible. Results are not capped.

`grepReviewCode` now requires an explicit `literal` or `regex` mode in its MCP
schema. Regex supports multiline anchors and alternation, returns complete matching
lines, and identifies malformed patterns rather than reporting an empty complete
search. The regex engine interrupts pathological matching with an execution
timeout. Already discovered matches are retained with partial coverage and an
actionable diagnostic; the timeout does not cap source or result size. The existing
locked regex dependency is now direct. Internal literal search remains the default
for application callers.

The dedicated offline local navigation and existing MCP tool tests passed 23 checks.
They cover exact snapshot differences, recursive glob semantics, missing overlays,
missing target coverage, unsafe paths/symlinks, all matches beyond common result
limits, long matching lines, regex alternation/anchors, invalid patterns, pathological
matching, and the actual generated MCP schemas. This verifies implementation
behavior only; no model, benchmark, service build, restart or deployment was run.

## Implemented verification lifecycle

An optional source-free verification planner groups cases whose outcomes depend on
the same concrete contract or missing fact. It retains every complete worklist;
malformed or overlapping groups preserve the original cases with diagnostics.

The verifier now exposes one `submitReviewStep` contract, forced where the provider supports tool choice, and one work ID
space. Each step assesses visible evidence before requesting batched read-only MCP
calls. Only pending work explicitly assessed `needs_evidence` can authorize reads;
accepted outcomes are committed first. Already settled questions cannot authorize
another component audit. Invalid IDs and malformed outcomes receive explicit
correction feedback. The competing `recordReviewDecisions` MCP control tool has
been removed.

Each call receives a canonical work/evidence packet instead of replaying assistant
prose and raw tool history. Overlapping exact source is represented once with
references; complete changed hunks and required source characters remain available.
A semantic fingerprint ignores query wording, receipt IDs and repeated observed
lines. Reads that add no facts or outcomes lead to an assessment of existing
observations before more retrieval. A stalled assessment leaves explicit
uncertainty. Formatting repair reuses current evidence. A valid structured request can recover
through a different concrete source lead; repeated unchanged evidence cannot
sustain the case. Provider generation endings remain observable.

The existing final comparison can flag specific contradictions between reports
under the same conditions. Only affected reports return to source checking using
their observed exact evidence. Case-local graph handles and private evidence IDs
are not reused across case namespaces. Ordinary deduplication remains source-free;
a contradiction flag cannot decide which claim is true. Auxiliary comparison or
adjudication failure preserves usable prior reports with diagnostics, including
structured uncertainty and stalled formatting. Only a source-supported refutation,
replacement or duplicate changes those existing publication records.

Offline genuine-provider checks exercise the single forced step with the installed
OpenAI, OpenRouter, Anthropic and Google adapters. In-memory HTTP transports check
OpenRouter JSON and SSE argument decoding and the actual tool-selection payload.
Updated fixtures cover complete source, independent-case isolation, malformed
response repair without transcript replay, and the server-rendered header defect.
These verify implementation and protocol behavior, not post-change quality, cost
or latency. No benchmark or paid model request was run.

The observations above remain a fixed capture snapshot, not a final result for
the benchmark that was still running during inspection.

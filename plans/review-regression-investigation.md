# Review regression investigation — 2026-09-25

This investigation concerns the four PRs in
`/var/www/html/codecrow-graph-calls-runner-10-2-luna6-mcp.json`, SHA-256
`2ff0188e9b9628629bbc418d797177a6fbaf6b4455afe364ea51143dd1a3fc7f`. It uses the already-produced review, existing judge output,
retained runtime logs, and repository objects at the recorded head revisions.
No benchmark, paid model call, service rebuild, or deployment was performed for
this investigation. Scripted model responses below verify host behavior, not
model precision, recall, F1, or production cost.

The user identified the reviewed OpenRouter model as **DeepSeek V4 Flash 0731**.
The `luna6` text in the benchmark artifact/tool label does not identify the model
that generated the review. The `openai_gpt-6-luna` directory below identifies the
existing evaluation/judge output; it likewise does not establish the review
provider/model. Provider-specific conclusions require the actual request records.

## Published duplicate failures

| PR | Posted comments, zero-based | Location | One shared mechanism |
| --- | --- | --- | --- |
| Cal.com 7232 | 0 and 1 | `packages/features/ee/workflows/api/scheduleEmailReminders.ts:57` | A rejected SendGrid request exits the shared loop/try block; later cancellations and the queued database-delete flush are skipped. |
| Discourse 6 | 0 and 1 | `app/serializers/user_serializer.rb:152` | `website_name` reveals information hidden by the `website` field's restricted-viewer condition. |

These are semantic duplicates, not merely matching titles or locations. The
second record adds detail to the first record's trigger, consequence, and fix.
The exact published descriptions are retained in
`python-ecosystem/inference-orchestrator/tests/fixtures/review_reconciliation/reported_duplicates.json`.

Retained runtime logs establish the sequence:

* Cal 7232 begins verification at `10:43:00.807Z` with five candidates. The
  `recordReviewDecisions` call at `10:52:11.135Z` settles six; verification ends at
  `10:52:46.705Z` with six candidates and two published records.
* Discourse 6 begins at `10:42:28.230Z` with two candidates. After
  `recordReviewDecisions` at `10:44:15.072Z`, two are settled and one remains
  pending. Verification ends at `10:44:45.487Z` with three candidates and two
  published records. Original `candidate-2` has no accepted final decision.

Thus each duplicate pair includes a verifier-created record in addition to an
original candidate. The original implementation's `VerificationState.record`
checked discoveries by full dictionary equality only against other discoveries,
never against original candidates. It immediately marked a discovery `keep`
unless the model happened to provide `duplicateOf`. Normal rewording therefore
created a second confirmed publication record. The log counts establish the
additional discoveries; exact model output is still required to attribute every
original ID and decision field, rather than infer it from text.

## Recall and validity evidence

### Cal.com 7232: a valid subclaim was lost when another subclaim was refuted

Recorded head: `6048e2a86b50e81e1e3b1b467dfea5a895add3dc`.

`handleCancelBooking.ts:485-493` invokes async email/SMS cancellation from nested
`forEach` callbacks. The promises are not collected. Lines 495-502 await unrelated
cleanup and return a successful cancellation response. `handleNewBooking.ts:966-978`
likewise launches cancellation without awaiting it, then starts rescheduling.
The helpers await external/database work internally; their own `catch` blocks
do not make their callers wait for completion.

The verifier correctly rejected the candidate's *unhandled rejection* wording:
the helper catches its own errors. It then rejected the missing-completion claim
because a serverless freeze or provider error was not concretely demonstrated.
Those are not prerequisites for establishing that completion is unsequenced.
The useful review question is whether cancellation must finish before success or
dependent rescheduling, and what happens when cancellation takes longer. A
counterexample supported by ordinary asynchronous semantics is sufficient;
observing an actual production freeze is unnecessary. The verifier should amend
the incorrect consequence while assessing the remaining causal claim.

The exact helper also exposes a separate question: its `immediateDelete` branch
posts a SendGrid cancellation and returns without updating/deleting the reminder
row. Whether this produces the golden comment's claimed lifecycle failure must
be assessed against the complete lifecycle, not copied from the benchmark label.

### Discourse 5: a reachable template was never resolved

Recorded head: `5b229316ee4c661836ed1161139692a3e8527444`.

`app/views/application/_header.html.erb` has `.contents > .row > .panel` and
`.contents > .row > .title`. The changed `header.scss` makes `.contents` a flex
container, removes `.panel { float: right }`, and replaces it with
`margin-left: auto`/`order(3)`. The panel is not a direct flex item in this server
template. This is concrete source needed to assess the lost right alignment.
The verifier instead ended with “Direct children of the header .contents element
were not resolved from source.” No visual design specification is needed to
discover this structural difference between the two header implementations.

Do not equate all golden labels with demonstrated regressions. The same PR adds
an invalid `-ms-align-items` declaration, but the same mixin already emits the
correct `-ms-flex-align`. The redundant invalid declaration does not by itself
establish a runtime defect. Similarly, an ordinal-group complaint requiring a
zero-valued caller should establish that call/contract rather than assume it.

### Discourse 4: different burdens of proof for accepted and rejected claims

Recorded head: `4f8aed295a29954023b2849c060ef4fb299d1b5d`.

The verifier made the `i.content.scrub` candidate uncertain because it had not
read the SimpleRSS missing-element behavior or an actual feed. It made the raw
URL interpolation candidate uncertain because no real attacker was observed.
External input contracts and a source-supported reachable counterexample should
be assessed explicitly. An actual malicious production actor is not required to
reason about a trust-boundary violation. Conversely, library behavior that is
uncertain really does need a contract source; a benchmark label alone does not
supply one.

One accepted comment claims `TopicEmbed.import_remote` returns `nil` on a dead
or rejected URL. At this exact head, `app/models/topic_embed.rb:44-53` calls
`open(url).read` and then `TopicEmbed.import`, with no rescue or host-validation
branch in `import_remote`. A fetch failure can raise instead. “Returns nil and
silently skips the thread” is therefore not the failure mechanism established
by that source. The verifier must distinguish a genuine related failure from
an invented return/error contract, and publish corrected supported wording.

## Existing paired judge output

The existing file
`persisted-beches/code-review-benchmark-full/offline/results/openai_gpt-6-luna/evaluations.json`
has these matched results for tool names
`CodeCrow-Graph-Calls-Runner-10-2-luna6` and
`CodeCrow-Graph-Calls-Runner-10-2-luna6-mcp`:

| PR | Earlier TP / FP / FN | Reported run TP / FP / FN |
| --- | --- | --- |
| Cal 7232 | 1 / 5 / 2 | 0 / 3 / 3 |
| Discourse 4 | 6 / 16 / 2 | 2 / 4 / 6 |
| Discourse 5 | 1 / 0 / 2 | 0 / 0 / 3 |
| Discourse 6 | 1 / 0 / 0 | 0 / 1 / 1 |
| Total | 9 / 21 / 6 | 2 / 8 / 13 |

These are the existing judge's counts, not a new evaluation. The judge splits
and deduplicates compound comments, so its candidate counts differ from posted
comment counts. Its labels also contain questionable matches: the earlier
Discourse 6 “true positive” is a frozen-string assertion about historical Ruby,
and one earlier Discourse 4 SSRF match names a different function from the golden
comment. The counts establish a regression in this existing four-case evaluation;
they do not validate every golden claim or quantify a larger unseen run.

## Actual call/context evidence

The retained inference container logs for these exact four PRs show:

| PR | Discovery calls / input tokens | Verification calls / input tokens | Largest verifier input |
| --- | --- | --- | --- |
| Cal 7232 | 10 / 73,543 | 14 / 310,703 | 31,809 |
| Discourse 4 | 28 / 73,565 | 32 / 992,003 | 61,507 |
| Discourse 5 | 5 / 16,695 | 5 / 39,022 | 9,430 |
| Discourse 6 | 5 / 15,520 | 18 / 201,400 | 16,589 |

Cal 7232 also made one cross-file call with 4,515 input tokens. Verification
totaled 69 calls, 1,543,128 input tokens, and 138,659 output tokens. These are usage
fields reported by the deployed model adapter, not a dollar-cost reconstruction.
Input cache hits and provider pricing must be considered separately for cost.
Discourse 4 started with 18 candidates plus 38 investigations, so one global
conversation still accumulated substantial unrelated work despite native tools.

At inspection, SHA-256 checks matched all six deployed/worktree files:
`verifier.py`, `verification_state.py`, `review_stages.py`, `planner.py`,
`model_calls.py`, and `agent_calls.py`. The current container start time was
`12:03:27Z`, later than these retained `10:xxZ` requests. Current byte identity
and matching diagnostics support the source attribution, but are not a historical
image digest captured at each call. Exact generation prompts/responses should be
used where available to strengthen that provenance.

## Canonical publication correction

`service/review/issue_reconciliation.py` provides one final semantic partition
over all confirmed records, including verifier discoveries. It receives complete
issue descriptions and locations, without repository source, graph envelopes,
tool schemas, source-review history, or extra repository reads. It cannot invent,
rewrite, dismiss, or revalidate an issue: the host selects existing representative
records from valid disjoint groups. Unaccounted or malformed groups preserve
verified issues with diagnostics. Zero/one issue and identical repeated records
require no call. Multiple distinct records require one source-free JSON call.

Scripted regressions use the two actual duplicate pairs and separate defects at
the same anchor, plus a cross-file duplicate case. They verify representative
selection, preservation on omission/overlap/provider outage, full causal text,
absence of source/tool payloads, and no retry loop. They do not demonstrate how
the production model will partition the records or promise an F1/cost improvement.

The reconciliation-specific suite contains 22 scripted checks. Final workflow,
provider transport and documentation validation is recorded below.

## Provider prompt inspection

The user approved inspecting the authenticated OpenRouter Logs window. The
following observations come from its stored request-message viewer, copied via
the normal UI, rather than reconstruction from current prompt templates. Raw
request messages remain in private `/tmp/codecrow-openrouter-*` files and are not
committed.

Generation `gen-1790333637-QeoRfel4NkK49vpYW5u3` is a DeepSeek V4 Flash 0731
request served by Wafer. The displayed usage is 37,351 input tokens, 223 output
tokens, and $0.00239. The UI attributes 33,792 input tokens to cache (90.5%);
therefore input volume alone must not be treated as a measured dollar-cost
regression. Its five exact messages were exported to
`/tmp/codecrow-openrouter-gen-1790333637-messages.json` (120,449 bytes).

This request concerns Cal.com date overrides, slot generation, and working-hour
boundaries. Its payload does not identify a PR number or title. It is **not** the
Cal 7232 email-reminder case above, and is not used as paired benchmark-quality
evidence. It demonstrates the deployed verifier's context behavior independently
of that result attribution.

The structured user message has 108,234 characters. Its serialized fields include:

| Field | Entries | Characters |
| --- | ---: | ---: |
| Pending candidates | 4 | 12,638 |
| Pending investigations | 6 | 4,772 |
| Retained evidence bodies | 24 | 82,431 |
| Evidence index | 26 | 5,532 |
| Settled issues | 0 | 2 |
| Answered investigations | 0 | 2 |

The last two evidence results are carried as native tool messages; their entries
also appear in the evidence index. This confirms real native tool use in this
request. The request's preceding assistant message called `grepReviewCode` twice,
for a booking-availability check and a date-override constructor. Both returned
`ready`, `complete=true`, and no results. The selected completion continued
investigating date-override construction and booking checks.

Concrete context defects visible in this request:

- All four hypotheses and six questions remain open after 26 tool reads, despite
  instructions to record settled work promptly. A single global session relies
  on the model to choose and finish a coherent scope before source can retire.
- A `queryCodeGraph(callees_of, getWorkingHours)` result alone contributes 19,728
  characters including its wrapper. Its 22 relationship rows repeat endpoint
  records and provenance; the results array is 18,091 characters, while its
  `sourceWindows` array is empty. This is navigation metadata, not 19 KB of new
  source evidence.
- Source remains represented both in complete changed hunks and file reads,
  alongside the entire earlier search history and an evidence index. Related
  questions separately restate offset-sign and storage-semantics premises already
  embedded in a long candidate explanation.
- The retained evidence includes `getStructuralUnit` called with the invented ID
  `placeholder`, returning a not-found error. Tool failure is visible, but the
  failed navigation attempt is still replayed with later unrelated work.
- The first candidate's confident timezone argument embeds an unverified library
  convention, while other pending questions ask whether that same convention is
  true. This creates duplicate investigation obligations and anchors later
  verification on the discovery narrative rather than a concise claim and its
  missing causal premise.

These observations support changing the working-context and decision lifecycle.
They do not establish the final disposition of this generation's hypotheses or
prove a precision/recall improvement. Final provider responses for the four
artifact PRs were not acquired: concurrent desktop input prevented further scoped
GUI inspection. Their duplicate and recall analysis above uses the published
artifacts, recorded runtime diagnostics and exact source, not an assertion that
all corresponding provider completions were read.

## Implemented corrections

The worktree now separates source verification into evidence cases derived from
changed-definition ownership. If ownership is absent or partial, the changed
file/side remains a complete scope. Direct changed-contract companions and exact
enclosing source acquired for discovery seed the case. Every selected source
body and hunk remains complete. An explicit cross-definition question receives
its named scope without transitively joining every PR question.

Each case appends actual native assistant/tool messages, preserving provider
metadata and stable prefixes. It ends as a unit before unrelated source enters
the next case. The model no longer manages `retainEvidenceIds` or receives a
rebuilt PR-wide evidence index each turn. A source request made alongside the
last verdict must still be consumed before the case closes; late counterevidence
can correct that verdict. Invalid native tool arguments receive a paired error
receipt, rather than leaving an unanswered call in the provider transcript.

Discovery now asks for the complete trigger, mechanism and consequence once in
`reason`, with `sourceLocations` pointers. It does not require three additional
versions of that explanation. A candidate's counterevidence checks should not
also become duplicate investigation questions. The verifier can correct a
partly wrong report at its original anchor while clearing superseded discovery
premises. An explicit different changed location cannot overwrite the original
finding. Exact unambiguous file/line coordinates can recover a mistyped hunk ID;
there is no nearest-line guessing or widening of incremental publication scope.

The final semantic partition receives all publication candidates, including new
verifier findings and explicitly retained partial fallbacks. It does not promote
partial results to verified findings. Case-local checks and final grouping both
preserve distinct failures that happen to share a line. The historical duplicate
pairs are committed as test fixtures, not as model-specific string filters.

Graph navigation responses can intern exact repeated unit records into a
`unitDefinitions` table. References preserve actual `unitId` values. This is
lossless representation sharing: every edge, unknown attribute, coverage flag,
cursor and source byte can be reconstructed. The captured Cal.com graph
observation changes from 18,462 to 15,248 compact-JSON bytes while retaining all
22 relationships. This is a representation-size measurement, not a token-cost
or quality claim. Semantic progress compares expanded records so a change in
representation alone cannot extend the agent loop. Graph cache identities also
include the effective case focus; unchanged local source remains reusable within
the same tenant/snapshot request.

Opt-in provider capture records final SDK HTTP request/response bodies, including
native tools and reasoning/routing parameters. Per-attempt manifests correlate
the run, tenant/project, PR/revision, queue job, stage, batch and provider generation.
Streams are copied as consumed; provider retries receive separate attempt files.
Headers and URL query credentials are omitted; repository source is retained
intact in private files. Capture failure leaves model execution available.
Nothing was enabled in the running deployment.

The public developer Docs and inference README describe the new behavior,
configuration, partial outcomes and cost implications. Their build was performed
in an isolated `/tmp` copy, leaving deployed site output and services untouched.
No new benchmark or paid generation was performed. The model's actual behavior
on a new paired run remains unmeasured; scripted checks do not establish F1,
precision, recall, latency or dollar-cost improvements.


## Final offline validation

The combined inference checks passed **288 tests**: review planning, source
context, case ownership, ledger revisions, semantic reconciliation, graph
projection, native protocol, exact request capture, provider factory/SSRF,
queue propagation and RAG client contracts. The suites also run isolated real-SDK
checks with mocked transports (17 native provider protocol checks, 17 HTTP capture
checks, and two case-continuation checks); these are not paid model evaluations.
One existing Pydantic forward-reference warning remains non-fatal.

The final isolated Docs build passed TypeScript, Vite, prerendering of 150 pages,
and SEO checks covering 6,264 root-relative anchors. Changed internal Docs links
were checked against the route definitions. Both repositories passed
`git diff --check`. Build evidence is in `/tmp/codecrow-verifier-docs-build.log`
and `/tmp/codecrow-verifier-docs-checks.json`.

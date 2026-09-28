# MCP5 regression investigation

The recovered `mcp-5` run regressed against `mcp-4` on the same fifteen pull requests, with identical base/head revisions and unchanged reference findings. This investigation uses existing benchmark outputs, judge artifacts, exact provider captures and source snapshots. It did not run another benchmark or make paid model calls.

## Paired quality result

These are micro-aggregated counts from the existing `openai_gpt-6-luna` judge output, including its candidate extraction and deduplication. They are measurements of that judge/reference corpus, not independent proof that every reference finding is correct.

| Corpus | Run | TP | FP | FN | Precision | Recall | F1 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Same 15 PRs, 61 reference findings | MCP4 | 24 | 34 | 37 | 41.38% | 39.34% | 40.34% |
| Same 15 PRs, 61 reference findings | MCP5 | 15 | 28 | 46 | 34.88% | 24.59% | 28.85% |
| All 25 PRs, 96 reference findings | MCP5 | 22 | 35 | 74 | 38.60% | 22.92% | 28.76% |

Paired F1 fell **11.49 percentage points, or 28.5% relatively**. The additional ten PRs do not explain the decline. Fifteen reference findings changed from detected to missed; six changed from missed to detected. On the paired corpus, published reports decreased from 55 to 37 while unresolved scopes decreased from 119 to 54. Fewer unresolved scopes did not imply better review quality.

The judge extracts individual claims from report prose: the paired runs contain 57 and 42 extracted claims respectively. MCP5 has 51 reports and 56 extracted claims across all 25 PRs. One candidate can match multiple reference findings, so TP plus FP is not necessarily the extracted-candidate count.

| PR | MCP4 TP/FP/FN | MCP5 TP/FP/FN | Lost reference matches | New reference matches |
| --- | --- | --- | --- | --- |
| Cal.com 8087 | 1/2/1 | 0/0/2 | Unawaited asynchronous iteration | — |
| Cal.com 10600 | 1/5/4 | 2/5/3 | Object URL cleanup | Concurrent backup-code reuse; case-sensitive backup-code comparison |
| Cal.com 10967 | 2/5/4 | 1/5/5 | Missing first destination calendar dereference | — |
| Cal.com 22345 | 0/0/2 | 0/2/2 | — | — |
| Cal.com 7232 | 1/3/2 | 0/0/3 | Unawaited reminder deletion | — |
| Cal.com 8330 | 0/0/2 | 0/0/2 | — | — |
| Cal.com 11059 | 5/3/4 | 2/4/7 | Uncaught request schema error; placeholder refresh token; persisted parser wrapper | — |
| Cal.com 14943 | 0/1/2 | 0/0/2 | — | — |
| Cal.com 14740 | 5/4/1 | 3/4/3 | Empty email validation; raw guest-list email routing; standard-email disable settings | Duplicate guest emails within one request |
| Cal.com 22532 | 1/2/3 | 1/3/3 | — | — |
| Discourse 1 | 2/1/2 | 1/2/3 | Duplicate method definition breaks an existing caller | — |
| Discourse 2 | 1/2/1 | 0/0/2 | Missing per-topic user record | — |
| Discourse 3 | 2/2/1 | 2/0/1 | Unanchored email-domain matching | Case-sensitive blocked-email lookup |
| Discourse 4 | 3/2/5 | 2/3/6 | Missing feed content; substring origin validation | Unescaped URL interpolation |
| Discourse 5 | 0/2/3 | 1/0/2 | — | Unsupported legacy flexbox property |

## Quota, deployment and judge freshness checks

The saved MCP5 output includes twenty resumed reviews and five successful reviews retained from the initial attempt: Discourse 1, 2 and 3, and Grafana 106778 and 107534. Quota-aborted initial attempts were excluded from the reviewed capture set. The retained and resumed captures contain 1,463 HTTP attempts: 1,461 successful responses and two rate-limit responses subsequently retried successfully. There are **no HTTP 403 quota failures** in these saved-run captures. All ten inspected deployed review, planner and protocol modules matched local source before this task's edits.

The judge's merged `benchmark_data.json` contains exactly the recovered MCP5 `review_comments` for all 25 PRs. The recovered output completed at 2026-09-28 02:16:29 UTC; merged input was written at 07:30:58, extraction at 07:31:10, deduplication at 07:31:13 and judging at 07:33:04. Every evaluated TP/FP candidate string occurs in the current extracted candidates, and all 25 judge entries have zero errors. There is no evidence that stale quota results, an old deployment or stale judge input caused this regression.

## Paired execution cost

The following cost is the sum of provider-returned `usage.cost`, not an independently reconciled invoice. It covers the same fifteen PRs used in the paired quality comparison.

| Measure | MCP4 | MCP5 |
| --- | ---: | ---: |
| Provider requests | 631 | 978 |
| Discovery requests | 196 | 196 |
| Verification requests | 398 | 748 |
| Prompt tokens | 8,284,872 | 17,289,331 |
| Completion tokens, including reasoning | 1,703,425 | 3,004,097 |
| Provider-reported cost, USD | 1.000966 | 1.868156 |
| Discovery cost, USD | 0.406034 | 0.427528 |
| Verification cost, USD | 0.536490 | 1.378731 |
| Responses ending with `length` | 3 | 17 |

Verifier behavior dominates the extra cost; discovery call count did not increase. Across all 25 saved reviews, provider-reported cost was USD 2.900019, with 26,602,059 prompt tokens and 4,673,034 completion tokens. These numbers describe the regressed execution, not the effect of the repairs in this task.

## Failures demonstrated by the captures

### Source retrieval displaced decisions

Thirty of 163 verifier cases never emitted an `assessReviewWork` outcome, leaving 150 supplied work items without any formal assessment. The verifier could freely select another read instead of settling its current evidence. Some responses' internal reasoning identified the defect, but no executable assessment followed. Internal reasoning is useful for diagnosing the protocol failure; it must not be parsed into public findings.

Cal.com 11059, case 2, contained eleven work items. It performed twelve reads without an outcome. The source shows `parseRefreshTokenResponse` returning a `safeParse` result wrapper, Google CalendarService persisting that wrapper as `credential.key`, and subsequent reads expecting the direct token shape. This is a concrete lost finding, not a source-backed refutation. Discourse 1, case 4, similarly read the duplicate Ruby `downsize` definitions and the existing five-argument `resize_emoji` caller over four turns without submitting a decision.

Cal.com 8330 remained at zero reports. Its case 1 combined four candidates and five questions, performed eleven turns and eighteen reads, and produced no assessment. Four responses exhausted provider completion space. The observed bounds and object-identity problems were never settled as protocol output.

### Shared source became excessive verification scope

The earlier deterministic case builder made a source owner an indivisible worklist, and the optional planner broadened groups around common contracts. Cal.com 7232 combined ten cases into 26 work items spanning parameter shape, nullability, asynchronous completion, cancellation flags and scheduled-job behavior. Cal.com 8330 mixed timezone arithmetic, day matching and object identity. Cal.com 11059 mixed parser wrappers, placeholder tokens, schema construction and error handling.

These mechanisms can need the same file without requiring one conversation to settle them together. Atomic failure work and reusable source evidence are separate concerns.

### Completion exhaustion and broad reads amplified the problem

All 36 saved-run responses ending with `length` were verifier responses reporting exactly 8,192 completion tokens and zero native tool calls. The captured requests supplied neither `max_tokens` nor `max_completion_tokens`; this was not a new application token cap.

An unscoped literal search for `expand` in Discourse 10, case 18, increased the next prompt from 9,688 to 105,587 tokens, with the matches retained in later packets. The repair is to use the concrete missing fact and known paths or definitions to select source. Truncating results would discard possible evidence without correcting the investigation.

### Valid explanations failed report promotion

Cal.com 14740, case 9, submitted confirmed outcomes three times with a changed anchor and source, but omitted the title from its nested issue. The assessment's explanation was already accepted as the report's default reason. The host's generic rejection failed to identify the missing title, sending a presentation correction back into source investigation. The case continued to 22 turns and 38 reads before stalling. Across all cases, the generic issue rejection appeared in 22 later feedback packets; that counts repeated feedback, not 22 different defects.

A separate published label report ended mid-sentence. Its provider response was complete native tool-call JSON with `finish_reason=tool_calls`; the host did not clip it. Report explanations need source-grounded, complete prose, while malformed presentation must not be confused with missing source.

## Discovery and semantic validation gaps

**Actual argument flow was missed.** In Cal.com 14740, the handler computes `uniqueGuests` by filtering existing attendees, but passes the original `guests` array into `sendAddGuestsEmails`. That function chooses a scheduled email when an attendee appears in its `newGuests` argument. A request containing an existing attendee and a new guest therefore misclassifies the existing attendee. Discovery did not create a finding or question for this flow, while the email investigation collected nine template, translation, calendar-file and import questions. The presence of filtering nearby does not establish that a consumer receives its result.

**A broad migration question never became a finite defect check.** In Cal.com 10967, changed callers can pass an empty destination-calendar array; EventManager takes its first element and accesses `.integration`. The previous code used optional chaining. A broad array-migration investigation repeatedly reread this code over 21 turns without an assessment. The specific absent-element mechanism and changed anchor should be handled independently of auditing every other consumer.

**An incorrect example caused a correct mechanism to be discarded.** Discourse 4 uses `discourseUrl.indexOf(e.origin)`. Discovery supplied an attack-domain example that does not pass that expression, and verification refuted it. The actual direction accepts a shorter prefix origin, such as a trusted `https://example.com` versus sender `https://example.co`. Correcting the witness for the same demonstrated mechanism differs from inventing a new defect.

**An unenforced caller restriction was treated as a guard.** Discourse 2's new route dereferences a possibly absent `TopicUser`. Verification argued that generating the original recipient's email creates that row, then dismissed a logged-in third party following a forwarded link as contrived. The HTTP route does not bind the authenticated user to the original email recipient. A producer's common use does not enforce all valid endpoint inputs.

**Adjacent behavior was used to invent an exception.** Cal.com 14740's add-guests email path does not check standard-email disable flags that neighboring scheduled-email code checks. Verification asserted that manual add-guests operations were exempt without establishing that exception, even though it reuses a scheduled-email class. This needs the actual settings contract; neither the reference label nor a plausible exemption is enough.

Long final reasons also introduced secondary claims that extraction judged independently, including diagnostic-message loss, missing test coverage and additional performance/error consequences. One verified defect should have one supported explanation and correction; unrelated assertions should not be appended to that report.

## Reference findings that should not be blindly restored

Some lost matches remain questionable or need a narrower report. Cal.com 8087 already had unawaited deletion promises and some asynchronous `forEach` callbacks in the base. One changed callback does newly move synchronous lookup errors into an unobserved promise, but a blanket report that all cleanup races were introduced is inaccurate. Cal.com 14740's blank email validation is not inherently a defect merely because a reference suggests a different initial array. Uppercase backup-code acceptance in Cal.com 10600 depends on a product contract, rather than hexadecimal representation alone. The new webhook decrypt/JSON error report and the earlier request-schema error report also describe different throw sites; they should not be treated as equivalent solely to recover a judge match.

## Repair boundaries and validation

Verification work now starts with one candidate or one concrete question per case. Source ownership still selects complete changed hunks and direct companion source. Optional grouping is limited to repeated descriptions or checks of the same precise suspected failure, with an explicit shared causal mechanism and correction/counterevidence. Malformed or unavailable plans preserve every original item. The planner no longer receives broad generated contract summaries as grouping authority; source reads remain shared through the existing request cache.

Discovery and cross-file instructions now emphasize the actual argument or persisted value passed to a consumer. A question must follow a concrete changed operation or value to a missing causal premise; unfamiliar imports or dependencies alone do not create an audit worklist. Substantive unknown caller and consumer obligations remain eligible. These changes do not increase reasoning effort, add model stages, impose source limits or introduce repository-specific production rules.

The verifier now requests an assessment before exposing its evidence-acquisition step, and returns to assessment after actual observations. The assessment records a concrete missing fact when source is required; native providers bind the outcome tool for assessment, while the evidence step exposes the scoped source tools. Existing evidence, settled sibling outcomes and report corrections survive the handoff. A new concrete source route remains eligible even after repeated observations; repeating the same tool arguments with a different purpose description does not reset stalled work. Report promotion reuses an assessment's final explanation and retains the rejected report's source location and citations for precise field repair.

Validation after integration: **467 offline review, provider, capture, model-adapter and RAG-client checks passed**. The real SDK transport fixtures exercise JSON and SSE assessment → evidence → assessment calls and global capture turn numbering. Targeted controller checks also exercise final assessment after repeated reads, recovery through a new source route, report-field repair without rereading, and source handoff while another report correction remains pending. The existing Pydantic settings forward-reference warning remains unrelated to these changes. A final focused run after aligning correction-message wording passed 100 checks.

Nine owning Docs pages and both README files were updated. An isolated `npm run build` passed with 150 canonical pages and 6,264 root-relative anchors; changed-source hashes matched the built copy. Fifteen internal Docs links and eight registered routes were checked. Build output stayed under `/tmp/codecrow-mcp5-docs-ju9wnrxe`, outside the served site.

Offline tests establish work preservation, source handling and protocol behavior; they cannot establish post-change precision, recall, cost or latency. Those remain unmeasured until a new paired execution and judgment. No service build, restart, redeployment or runtime configuration change was used for this investigation.

## Evidence locations

Reproducible source artifacts are the MCP4 and MCP5 benchmark JSON files and `persisted-beches/code-review-benchmark-full/offline/results/openai_gpt-6-luna/{candidates,dedup_groups,evaluations}.json`. Exact base/head references are retained in each review's `codecrow_diagnostics.mcpContract` and raw response.

Private working indexes are under `/tmp/codecrow-mcp5-audit/`, `/tmp/codecrow-mcp5-captures/` and `/tmp/codecrow-mcp5-outcomes/`. They include the paired judge comparison, exact lost/gained matches, deployed hashes, saved-run captures, call statistics, raw-result diagnostics and source excerpts. This durable report deliberately excludes captured source bodies, credentials, tenant bindings and private provider reasoning.

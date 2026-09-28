# September 28 review investigation

The `mcp-4` execution used `deepseek/deepseek-v4-flash-0731` through OpenRouter's Wafer route. Debug wire capture worked. This investigation used saved requests and responses rather than making new provider calls or rerunning the benchmark.

The selected window is September 27, 2026 21:00 UTC through September 28 16:34 UTC. The 631 completed captured calls in that window ran from September 27 21:06:27 through 22:08:56 UTC and cover the 15 PRs in `codecrow-graph-calls-runner-10-2-luna6-mcp-4.json`. Before editing, all eleven inspected deployed review modules matched their local source hashes: verifier, verification state, verification planning, verification context, review service, review stages, agent calls, review step, local source, verification tools, and issue reconciliation. This was an execution of the preceding implementation, not a stale deployment.

## Observed call distribution

| Stage | Calls | Input tokens | Output tokens | Captured provider cost (USD) |
| --- | ---: | ---: | ---: | ---: |
| Discovery | 196 | 1,420,570 | 936,950 | 0.406034 |
| Verification planning | 14 | 164,637 | 67,503 | 0.033317 |
| Verification | 398 | 6,522,542 | 656,966 | 0.536490 |
| Reconciliation | 12 | 23,854 | 17,063 | 0.007363 |
| Cross-file review | 11 | 153,269 | 24,943 | 0.017762 |
| Total | 631 | 8,284,872 | 1,703,425 | 1.000966 |

Cost is the sum of `usage.cost` returned in these saved provider responses, not an independently reconciled invoice. Output tokens include provider reasoning. These figures describe this execution only; they do not establish a before/after quality, latency, or cost improvement.

## Control failures visible in the captures

**Scoped reads were suppressed unless the same response repeated `needs_evidence`.** There are 114 skipped evidence-request groups visible in subsequent prompts across 45 of 102 verification cases. The diagnostic was `No named work remains pending with needs_evidence`. The controller computed eligibility from the current response's assessments instead of from pending work. A valid read for a still-pending question was therefore skipped when the model omitted the redundant assessment, or expressed its uncertainty alongside the read.

Discourse PR 5, case 3 demonstrates a resulting false negative. The first turn requested header navigation. The second requested the actual header templates, including the server-rendered header, but had an empty assessments array. Those reads were suppressed. The third turn repeated the reads and the case stopped without examining the nesting that determines the header layout. The second-turn generation is `gen-1790544722-gqDCsKxmBHDXNNVa8k0x`.

**The nested submission function accepted empty output as a valid native call.** There are 38 all-empty submissions across 31 distinct cases. They have empty assessments, evidence requests, and findings, despite pending work. This is separate from malformed JSON or length termination. In Cal.com PR 8330, case 1, discovery had already identified the working-hours end calculation using `slotStartTime` and had raised the distinct-Dayjs-instance equality question. Both verification turns returned empty arrays, dropping all four candidates and leaving four questions unanswered. The first generation is `gen-1790546112-hNIyZukG6pKLx1JF9vR6`.

**Generated hypotheses were treated as requirements.** Discourse PR 5's paragraph-spacing finding was confirmed by citing a generated contract hint as the reason the old spacing must persist. The badge-offset report similarly depended on a generated statement that the old margin “must be preserved.” The source showed a style change; the generated summaries supplied the unproven requirement. Relevant generations are `gen-1790544692-iuZzZRkAMXdc4CRAyIYP` and `gen-1790544719-FhULcOFaib38L1F5Leuz`.

**Some failures remain provider-generation failures.** Three verifier responses ended with `finish_reason=length`, each reporting 8,192 output tokens (Discourse PR 4, Cal.com PRs 11059 and 14740). No captured request contained `max_tokens` or `max_completion_tokens`. The application sent only the configured reasoning effort for this purpose, so this cutoff was not a newly imposed application output cap. A different tool protocol cannot guarantee that a provider will always produce a usable generation.

## Source checks and discovery gaps

All 15 PRs produced terminal responses, but 14 were partial, with 119 unresolved scopes across the execution. The run published 55 reports against 61 reference issues; these are counts, not precision or recall measurements. This investigation did not rerun the judge or assume that every reference issue was correct.

- **Cal.com 7232:** the verifier confirmed an unused-import build failure while requesting `tsconfig` to establish whether unused imports actually fail the build. The old host closed the work and skipped that read. The repository's inherited configuration sets `noUnusedLocals: false`; the claimed compilation failure lacked its required premise.
- **Discourse 2:** a report assumed an Ember computed property was read-only. The vendored implementation defaults `_readOnly` to false, and the changed property does not call `.readOnly()`. Inferring library behavior from a property declaration produced an unsupported failure claim.
- **Cal.com 8087:** the reported asynchronous cancellation race already existed in the base implementation: it used `forEach(async ...)` and awaited deletion before appending results. Touching that path did not introduce the reported race.
- **Cal.com 10600:** verification found the actual parent form's `noValidate`, but publication retained discovery's hypothetical unseen-parent explanation. Separately, discovery missed the non-atomic backup-code read/decrypt/invalidate/write sequence, so correcting the verifier alone cannot recover that omission.
- **Cal.com 14740:** discovery did not identify repeated identical guest emails within a single request. The input array, filtering and insertion path permit that concrete case. This is distinct from the case-normalization issue discovery did report.
- **Cal.com 10967:** discovery omitted a missing credential passed into a dereferencing callee for additional calendar references newly processed by the loop. The base already failed when its first reference lacked credentials, so an introduced-regression explanation must distinguish the additional-reference trigger.
- **Cal.com 11059:** discovery noted a credential ID versus user ID argument mismatch in its summary but did not route it as a finding or source question. Describing a potential incompatibility only in internal summary text does not create verification work.

Some benchmark disagreements require restraint. For Cal.com 14943, the verifier inspected the writers of the new retry counter and refuted a cross-channel retry allegation because the available source did not establish the necessary non-SMS state. That is not evidence that the controller should automatically retain the reference allegation. Sanitized regression fixtures exercise source and context handoffs; they do not encode benchmark-specific production rules or prove future model verdicts.

## Implementation response

The verifier now exposes the actual request-bound read tools as ordinary native functions. Each read includes the pending work IDs and the concrete missing fact it will establish; those two scope fields are removed before dispatch to the underlying MCP tool. A single `assessReviewWork` tool accepts a nonempty list of assessments for the shared work-ID space. Provider-supported tool choice requires a tool call while allowing the model to choose the needed source or assessment function. Models without native binding use the same tool names and arguments through JSON. There is no nested union of all evidence tools inside a forced submission function, and no separate candidate-versus-question tool contract.

The host normalizes tool calls into its internal assessment/request representation. It commits independent outcomes before retrieval and defers outcomes that request their own missing evidence until that source can be assessed. Valid sibling assessments survive malformed or unscoped sibling calls. Unknown tool names cannot reach MCP dispatch. These changes add no source, token, file, or read-count cap, and no production shell tool.

The accompanying controller and evidence changes address pending-work retrieval, uncertainty handling, and publication of the final verified explanation. A requested fact defers any outcome for the same work; independent outcomes settle. Conflicting terminal assessments for one work item in one response remain provisional for explicit repair instead of depending on call order. Discovery uses medium reasoning effort and follows changed values, effects, guards and before/after behavior; source-free routing remains low effort. Concrete incompatibilities noticed in a summary must also be routed as findings or source questions, depending on the evidence already available. Generated summaries and grouping rationale are routing aids, not authoritative behavioral requirements. Exact source and the actual change purpose remain the basis for a finding.

## Evidence and verification limits

Private working evidence is under `/tmp/codecrow-sep28-captures/`: `index.json` contains the 631 parsed wire records, `verification-cases.json` contains the 102 case groups, `protocol-feedback.json` contains structured correction feedback, `inference.log` contains the selected service log window, and `deployed-hashes.json` records the pre-edit deployment comparison. The directory is private and copied capture files have owner-only permissions. Full captured source and provider reasoning are not reproduced in this report.

The protocol checks use real installed OpenAI, OpenRouter, Anthropic, and Google SDKs with intercepted offline JSON and SSE transports. JSON capture checks also exercise Google Vertex and OpenAI-compatible adapters. They check source preservation, scoped function arguments, invalid-sibling recovery, and the absence of protocol retries after paid-request failure. They are architecture and protocol checks, not model-quality measurements. No benchmark, paid model call, service build, restart, redeploy, or runtime configuration change was performed for this investigation.

## Completed checks

The focused inference suite passed 452 tests across review orchestration, source handling, verification state/workflow, reconciliation, provider capture, model construction, JSON parsing and the RAG client. Its subprocess checks use genuine provider SDKs with offline transports, including OpenRouter JSON and streaming responses. A pre-existing Pydantic dependency warning remains; there were no test failures. Regression coverage includes a reopened source question keeping its derived finding pending until an explicit disposition, rather than silently dropping it.

Eight existing developer Docs pages were updated. An isolated `npm run build` passed type checking, the client build, prerendering of 150 pages and SEO checks. Sixteen changed-page internal link occurrences resolved to seven registered routes. The built Docs source was checked against the workspace files, and code/Docs whitespace checks passed. The Docs build used command-local public URL values in the temporary copy to avoid the existing local environment URLs; service configuration was not modified.

These checks establish controller, evidence and protocol behavior only. Post-change precision, recall, token use, cost and latency remain unmeasured until the next paired benchmark.

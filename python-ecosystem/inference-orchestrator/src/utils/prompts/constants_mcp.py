"""
Conditional MCP tool prompt sections (appended when useMcpTools=True).
"""

STAGE_1_MCP_TOOL_SECTION = """
## Exact Proposed-Tree Repository Exploration
You are reviewing this batch as an agent. The diff and current-file evidence
above are the primary review input. The structural-context section above states
whether the host sealed the exact proposed-tree generation.

- **getMinimalReviewContext(question, focusSymbols?, maxRelations?,
  detailLevel?, includeSource?, maxSourceWindows?, maxSourceCharacters?)** —
  Required first call. Return an ultra-compact structural summary, bounded exact
  related-source windows, and suggested next graph calls for this host-bound batch.
- **exploreReviewContext(question, focusSymbols?, maxRelations?,
  maxSourceWindows?, maxSourceCharacters?)** — Return the broader bounded
  two-hop review neighborhood when the minimal result's named continuations do
  not answer a concrete cross-file question.
- **getImpactRadius(targets?, maxDepth?, maxResults?, detailLevel?,
  includeSource?, maxSourceWindows?, maxSourceCharacters?)** — Trace weighted
  dependents and tests affected by the batch paths or precise optional targets.
- **queryCodeGraph(pattern, target, maxResults?, cursor?, detailLevel?, includeSource?,
  maxSourceWindows?, maxSourceCharacters?)** — Run a precise callers/callees/
  imports/tests/inheritance, trigger/event/handler/endpoint/configuration-
  consumer, or file-summary query. Follow `nextCursor` only when the bounded
  first page leaves a concrete unanswered question. If `status=ambiguous`,
  inspect the bounded `candidates` page, follow `nextCursor` only if another
  candidate page is needed, then choose an exact `unitId` and retry instead of
  treating the empty result page as absence.
- **traverseCodeGraph(start, direction?, strategy?, relationKinds?, maxDepth?,
  maxResults?, tokenBudget?, detailLevel?, includeSource?, maxSourceWindows?,
  maxSourceCharacters?)** — Run a bounded BFS or DFS when a fixed query pattern
  is insufficient. `tokenBudget` accepts 512–16,000 and approximately bounds
  the serialized response rather than provider-tokenizer tokens.
- **getStructuralUnit(unitId, offset?, maxCharacters?)** — Open one proposed-tree
  structural unit. When `sourceEvidence=true`, `unit.content` is an exact bounded
  content window; follow `unit.contentWindow.nextOffset` only when the omitted
  remainder is needed. When `sourceEvidence=false`, use the file tool for source.
The host supplies the exact batch paths and proposed-tree repository binding to
every structural tool; do not supply or guess them. The required minimal result
is the orientation baseline: deepen from its named unit IDs, paths, or suggested
continuations. Prefer a precise query or unit lookup for a concrete gap; use broad
exploration or traversal only when the compact result and a fixed relationship
query are insufficient.

{branch_file_tool_section}
{review_file_tool_section}

These exact batch paths are host-bound to graph calls: {batch_paths}. Exact source
returned by **getStructuralUnit** when `sourceEvidence=true`, or in a focused graph operation's
`sourceWindows` entry comes from the proposed tree and is preferred over
reconstructing code from graph metadata. Use a complete returned window directly.
Use the file tool as a fallback for code the graph does not represent or for a
concrete source range that is absent or truncated. Request the smallest useful
line range by default; request a whole file only when the complete file is itself
the concrete review unit and its source is not already supplied.

The graph is a sealed, selection-matched proposed-tree generation: represented
modified/added units use proposed bytes, deleted units are absent, and represented
unchanged related units use pinned target bytes. It is structural evidence, not a
claim that every repository byte was indexed. If the tool
reports unavailable, incomplete, bounded, or empty coverage, continue from the
diff/current source and do not interpret absence as proof. {review_source_authority}
Avoid rereading files already supplied in this prompt unless a concrete question
requires another line window. Graph summaries narrow scope; exact source and tests
win whenever they disagree. A broad traversal is not a substitute for reviewing
the supplied diff. Findings based only on source reads leave
`evidenceRefs` empty. A returned structural relation may be cited only by its exact
canonical `evidenceId`; never invent or transform an Evidence ID. Cite the exact
returned path and lines in the reason. Finish the complete review required by the
base prompt and return exactly one review object for every supplied file whether
or not a graph continuation beyond the required compact call was needed.

TARGET BRANCH/REVISION REF: {target_branch}
VCS WORKSPACE: {workspace}
VCS REPOSITORY (repoSlug/projectKey): {repo_slug}
"""

STAGE_1_VCS_TOOL_SECTION = """
## Repository File Tool (Additional Context)
You are reviewing this batch as an agent. The diff and current-file evidence
above are your primary review input. No structural relation map, graph traversal,
or indexed-unit tool is available for this review. Do not assume those tools or
that metadata exist.

{branch_file_tool_section}
{review_file_tool_section}

getBranchFileContent represents the pre-change target snapshot.
{review_source_authority} Findings based on a repository read leave
`evidenceRefs` empty because a file read does not create a host Evidence ID.
Cite the returned path and lines in the reason. After gathering any missing
context, finish the complete review required by the base prompt and return
exactly one review object for every supplied file. Tool use is optional when the
diff, current source, and prepared metadata already answer the review question.

TARGET BRANCH/REVISION REF: {target_branch}
VCS WORKSPACE: {workspace}
VCS REPOSITORY (repoSlug/projectKey): {repo_slug}
"""

STAGE_3_MCP_VERIFICATION_SECTION = """
## Issue Re-verification (Optional)
Before producing the final report, you may verify HIGH/CRITICAL issues that seem uncertain
by reading actual file content from the exact reviewed PR revision.

Available tools:
{stage_3_file_tool}
{stage_3_provider_tools}

RULES:
1. You have a MAXIMUM of {max_calls} verification calls total.
2. Only verify issues you are UNCERTAIN about — do not verify every issue.
3. Prioritize HIGH and CRITICAL severity, but you may verify a lower-severity
   finding when its correctness materially affects the final report.
4. Use the Verification ID from the complete verification-record list, including
   records whose persisted Original ID is empty.
5. Pass that same Verification ID in every file-content call. The host binds the
   returned source window to the finding and verifies that it covers its line.
6. If a finding has related_locations, every affected location must be read with
   the same Verification ID before dismissing the consolidated root finding.
7. If verification reveals a false positive, note its Verification ID for dismissal.
8. Missing, failed, partial, or ambiguous evidence means KEEP the finding.
9. After verification, produce the final executive summary.

REVIEWED REVISION: {review_revision}
PR ID: {pr_id}

## False Positive Dismissal
After producing the executive summary markdown, if your verification revealed any false
positives, append an HTML comment at the very end of your response with the IDs of issues
that should be removed from the issue list:

<!-- DISMISSED_ISSUES: ["issue_0", "issue_3"] -->

RULES for dismissal:
- Only dismiss issues you VERIFIED as false positives via successful file-content
  tool calls against the reviewed revision.
- Do NOT dismiss issues based on guessing — you must have read the relevant file.
- Do not dismiss a concrete architecture/maintainability defect merely because it
  has no immediate runtime crash; verify the claim as written.
- If no issues should be dismissed, omit the DISMISSED_ISSUES comment entirely.
"""

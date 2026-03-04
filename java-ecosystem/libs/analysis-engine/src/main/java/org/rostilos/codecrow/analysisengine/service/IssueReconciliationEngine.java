package org.rostilos.codecrow.analysisengine.service;

import org.rostilos.codecrow.analysisengine.util.DiffParsingUtils;
import org.rostilos.codecrow.astparser.api.ScopeResolver;
import org.rostilos.codecrow.astparser.model.ParsedTree;
import org.rostilos.codecrow.astparser.model.ScopeInfo;
import org.rostilos.codecrow.core.model.codeanalysis.IssueScope;
import org.rostilos.codecrow.core.util.tracking.LineHashSequence;
import org.rostilos.codecrow.core.util.tracking.ReconcilableIssue;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.*;

/**
 * Shared, entity-agnostic reconciliation engine used by <em>both</em> the branch
 * and PR reconciliation paths.
 * <p>
 * Every method operates on {@link ReconcilableIssue} — the common interface
 * implemented by both {@code BranchIssue} and {@code CodeAnalysisIssue}.
 * The engine is <b>stateless</b> and never persists anything; it returns
 * structured results that callers apply to their own entity type.
 * <p>
 * Core capabilities:
 * <ul>
 *   <li>{@link #remapLinesFromDiff} — diff-based line-number reconciliation</li>
 *   <li>{@link #verifySnippetAnchors} — snippet-based line-number verification</li>
 *   <li>{@link #classifyByContent} — deterministic content-based classification</li>
 *   <li>{@link #sweepDeterministic} — full sweep for stale issues via hash comparison</li>
 * </ul>
 *
 * <h3>Scope-aware rules</h3>
 * <ul>
 *   <li><b>LINE</b> — standard: auto-resolve when snippet/hash gone</li>
 *   <li><b>BLOCK / FUNCTION</b> — auto-resolve only when ALL snippet lines gone</li>
 *   <li><b>FILE</b> — never auto-resolve (only AI or file deletion resolves these)</li>
 * </ul>
 */
@Service
public class IssueReconciliationEngine {

    private static final Logger log = LoggerFactory.getLogger(IssueReconciliationEngine.class);

    /**
     * Result of a diff-based line remap for a single issue.
     */
    public record LineRemapResult(
            ReconcilableIssue issue,
            int oldLine,
            int newLine
    ) {
        public boolean changed() {
            return oldLine != newLine;
        }
    }

    /**
     * Result of snippet-based verification for a single issue.
     */
    public record SnippetVerificationResult(
            ReconcilableIssue issue,
            int correctedLine,
            String correctedLineHash,
            String correctedContextHash
    ) {}

    /**
     * Classification result from deterministic content tracking.
     */
    public enum Classification {
        /** Content anchor found — issue still exists at a known location. */
        CONFIRMED,
        /** Content anchor is gone from the file — issue is resolved. */
        RESOLVED,
        /** No reliable anchor; needs AI reconciliation. */
        NEEDS_AI
    }

    /**
     * Result of classifying a single issue by content.
     */
    public record ContentClassification(
            ReconcilableIssue issue,
            Classification classification,
            /** Updated line number (set for CONFIRMED issues, null otherwise). */
            Integer updatedLine,
            /** Updated line hash (set for CONFIRMED issues, null otherwise). */
            String updatedLineHash
    ) {}

    /**
     * Result of a deterministic sweep for a single issue.
     */
    public record SweepResult(
            ReconcilableIssue issue,
            /** True if the issue's content anchor is completely gone. */
            boolean resolved,
            /** Reason string for logging / persistence. */
            String reason
    ) {}

    // ═══════════════════════════════════════════════════════════════════════
    //  1. Diff-based line remapping
    // ═══════════════════════════════════════════════════════════════════════

    /**
     * For each issue in {@code issues}, compute the new line number after applying
     * the given diff. Returns only issues whose line actually changed.
     *
     * @param issues      issues to remap (must share the same file path)
     * @param fileDiff    the unified diff section for the file
     * @return list of remap results (only those that changed)
     */
    public List<LineRemapResult> remapLinesFromDiff(
            List<? extends ReconcilableIssue> issues,
            String fileDiff) {

        if (fileDiff == null || fileDiff.isBlank() || issues.isEmpty()) {
            return List.of();
        }

        List<int[]> hunks = DiffParsingUtils.parseHunks(fileDiff);
        if (hunks.isEmpty()) {
            return List.of();
        }

        List<LineRemapResult> results = new ArrayList<>();
        for (ReconcilableIssue issue : issues) {
            Integer lineNum = issue.getLine();
            if (lineNum == null || lineNum <= 0) {
                continue;
            }

            int newLine = DiffParsingUtils.mapLineNumber(lineNum, hunks, fileDiff);
            if (newLine != lineNum) {
                results.add(new LineRemapResult(issue, lineNum, newLine));
            }
        }
        return results;
    }

    /**
     * Re-verify issue line numbers using their persisted {@code codeSnippet}.
     * Catches any drift that diff-based remapping missed.
     * <p>
     * Only issues that have a non-blank snippet and a positive line number
     * are checked. Issues whose current line already matches a snippet line
     * are left untouched.
     *
     * @param issues       issues to verify (single file)
     * @param lineHashes   the {@link LineHashSequence} for the current file content
     * @return list of corrections (only issues that were corrected)
     */
    public List<SnippetVerificationResult> verifySnippetAnchors(
            List<? extends ReconcilableIssue> issues,
            LineHashSequence lineHashes) {

        if (issues.isEmpty() || lineHashes.getLineCount() == 0) {
            return List.of();
        }

        List<SnippetVerificationResult> results = new ArrayList<>();

        for (ReconcilableIssue issue : issues) {
            String snippet = issue.getCodeSnippet();
            if (snippet == null || snippet.isBlank()) {
                continue;
            }

            Integer currentLine = issue.getLine();
            if (currentLine == null || currentLine <= 0) {
                continue;
            }

            // Handle multi-line snippets: check each line individually
            String[] snippetLines = snippet.split("\\r?\\n");
            boolean alreadyMatches = false;
            int bestFoundLine = -1;
            int bestDist = Integer.MAX_VALUE;

            for (String snippetLine : snippetLines) {
                if (snippetLine == null || snippetLine.isBlank()) continue;
                String lineHash = LineHashSequence.hashLine(snippetLine);

                // Check if current line already matches this snippet line
                if (!alreadyMatches && currentLine <= lineHashes.getLineCount()) {
                    String currentHash = lineHashes.getHashForLine(currentLine);
                    if (lineHash.equals(currentHash)) {
                        alreadyMatches = true;
                    }
                }

                // Find closest match for this snippet line
                int foundLine = lineHashes.findClosestLineForHash(lineHash, currentLine);
                if (foundLine > 0) {
                    int dist = Math.abs(foundLine - currentLine);
                    if (dist < bestDist) {
                        bestDist = dist;
                        bestFoundLine = foundLine;
                    }
                }
            }

            // If current line already matches some snippet line, no correction needed
            if (alreadyMatches) {
                continue;
            }

            // Line drifted — return correction
            if (bestFoundLine > 0 && bestFoundLine != currentLine) {
                results.add(new SnippetVerificationResult(
                        issue,
                        bestFoundLine,
                        lineHashes.getHashForLine(bestFoundLine),
                        computeContextHash(lineHashes, bestFoundLine,
                                issue.getEndLineNumber(), issue.getCodeSnippet())
                ));
            }
        }

        return results;
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  3. Content-based classification (deterministic tracking stage)
    // ═══════════════════════════════════════════════════════════════════════

    /**
     * Classify each issue as CONFIRMED, RESOLVED, or NEEDS_AI based on whether
     * its content anchor (codeSnippet / lineHash) still exists in the current file.
     * <p>
     * <b>Scope-aware rules:</b>
     * <ul>
     *   <li>FILE scope → always NEEDS_AI (cannot be deterministically resolved)</li>
     *   <li>LINE scope → resolve when snippet/hash gone</li>
     *   <li>BLOCK/FUNCTION → resolve only when ALL snippet lines are gone</li>
     *   <li>Unanchored (line ≤ 1, no snippet) → NEEDS_AI</li>
     * </ul>
     *
     * @param issues       issues to classify (single file)
     * @param currentHashes line hashes for the current file content
     * @return per-issue classification
     */
    public List<ContentClassification> classifyByContent(
            List<? extends ReconcilableIssue> issues,
            LineHashSequence currentHashes) {

        List<ContentClassification> results = new ArrayList<>();

        // Guard: if file content is empty/unavailable, we cannot determine content state.
        // Route ALL issues to AI instead of risking false RESOLVED classification.
        if (currentHashes.getLineCount() == 0) {
            for (ReconcilableIssue issue : issues) {
                results.add(new ContentClassification(issue, Classification.NEEDS_AI, null, null));
            }
            return results;
        }

        for (ReconcilableIssue issue : issues) {
            Integer currentLine = issue.getLine();
            IssueScope scope = issue.getEffectiveScope();

            // ── FILE-scope issues: always AI ──
            if (scope == IssueScope.FILE) {
                results.add(new ContentClassification(issue, Classification.NEEDS_AI, null, null));
                continue;
            }

            // ── Unanchored issues: always AI ──
            boolean hasNoReliableAnchor = (currentLine == null || currentLine <= 1)
                    && (issue.getCodeSnippet() == null || issue.getCodeSnippet().isBlank());
            if (hasNoReliableAnchor) {
                results.add(new ContentClassification(issue, Classification.NEEDS_AI, null, null));
                continue;
            }

            boolean contentFound = false;
            String updatedLineHash = null;
            int updatedLine = currentLine != null ? currentLine : 1;

            // 1st priority: codeSnippet — handle multi-line snippets
            if (issue.getCodeSnippet() != null && !issue.getCodeSnippet().isBlank()
                    && currentHashes.getLineCount() > 0) {

                String[] snippetLines = issue.getCodeSnippet().split("\\r?\\n");
                int bestFoundLine = -1;
                int bestDist = Integer.MAX_VALUE;
                int matchedSnippetLines = 0;
                int totalNonBlankSnippetLines = 0;

                for (String snippetLine : snippetLines) {
                    if (snippetLine == null || snippetLine.isBlank()) continue;
                    totalNonBlankSnippetLines++;
                    String lineHash = LineHashSequence.hashLine(snippetLine);
                    int foundLine = currentHashes.findClosestLineForHash(
                            lineHash, currentLine != null ? currentLine : 1);
                    if (foundLine > 0) {
                        matchedSnippetLines++;
                        int dist = Math.abs(foundLine - (currentLine != null ? currentLine : 1));
                        if (dist < bestDist) {
                            bestDist = dist;
                            bestFoundLine = foundLine;
                        }
                    }
                }

                if (bestFoundLine > 0) {
                    // For BLOCK/FUNCTION scope, require ALL snippet lines present
                    if ((scope == IssueScope.BLOCK || scope == IssueScope.FUNCTION)
                            && totalNonBlankSnippetLines > 1
                            && matchedSnippetLines < totalNonBlankSnippetLines) {
                        // Some but not all snippet lines found — partial match
                        // Treat as NEEDS_AI for compound issues
                        results.add(new ContentClassification(issue, Classification.NEEDS_AI, null, null));
                        continue;
                    }

                    updatedLine = bestFoundLine;
                    updatedLineHash = currentHashes.getHashForLine(bestFoundLine);
                    contentFound = true;
                }
            }

            // 2nd priority: lineHash
            if (!contentFound && issue.getLineHash() != null
                    && currentHashes.getLineCount() > 0) {
                int foundLine = currentHashes.findClosestLineForHash(
                        issue.getLineHash(), currentLine != null ? currentLine : 1);
                if (foundLine > 0) {
                    updatedLine = foundLine;
                    updatedLineHash = issue.getLineHash();
                    contentFound = true;
                }
            }

            if (contentFound) {
                // ── Data-driven context hash comparison ──
                // The anchor line itself is present, but for BLOCK/FUNCTION scope
                // issues we also check whether the surrounding code window changed.
                // A different context hash means the function/block body was modified,
                // so the issue needs AI re-evaluation even though the anchor line is intact.
                // The window is derived from actual issue data (endLine / snippet),
                // NOT from hardcoded radius values.
                if (issue.getLineHashContext() != null
                        && (scope == IssueScope.BLOCK || scope == IssueScope.FUNCTION)) {
                    String currentContextHash = computeContextHash(
                            currentHashes, updatedLine,
                            issue.getEndLineNumber(), issue.getCodeSnippet());
                    if (currentContextHash != null
                            && !currentContextHash.equals(issue.getLineHashContext())) {
                        log.debug("Context hash changed for {} issue at line {} — " +
                                        "anchor line intact but body modified, sending to AI.",
                                scope, updatedLine);
                        results.add(new ContentClassification(
                                issue, Classification.NEEDS_AI, updatedLine, updatedLineHash));
                        continue;
                    }
                }

                results.add(new ContentClassification(
                        issue, Classification.CONFIRMED, updatedLine, updatedLineHash));
            } else if (hasAnyReliableAnchor(issue)) {
                results.add(new ContentClassification(
                        issue, Classification.RESOLVED, null, null));
            } else {
                results.add(new ContentClassification(
                        issue, Classification.NEEDS_AI, null, null));
            }
        }

        return results;
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  4. Deterministic sweep (hash-only, zero AI cost)
    // ═══════════════════════════════════════════════════════════════════════

    /**
     * Check a batch of issues against current file content using only hash-based
     * comparison. Issues whose content anchor (codeSnippet or lineHash) is completely
     * absent from the file are marked as resolved.
     * <p>
     * <b>Scope-aware:</b> FILE-scope issues are skipped (never auto-resolved).
     * BLOCK/FUNCTION issues require ALL snippet lines to be absent before resolving.
     *
     * @param issues       issues to sweep (single file)
     * @param currentHashes line hashes for the current file content
     * @return per-issue sweep result
     */
    public List<SweepResult> sweepDeterministic(
            List<? extends ReconcilableIssue> issues,
            LineHashSequence currentHashes) {

        List<SweepResult> results = new ArrayList<>();

        // Guard: if file content is empty/unavailable, we cannot determine whether
        // the code is truly gone. Skip all issues rather than falsely resolving them.
        // This prevents false resolutions when the VCS API returns empty content.
        if (currentHashes.getLineCount() == 0) {
            for (ReconcilableIssue issue : issues) {
                results.add(new SweepResult(issue, false,
                        "File content empty or unavailable — skipped (not resolved)"));
            }
            return results;
        }

        for (ReconcilableIssue issue : issues) {
            IssueScope scope = issue.getEffectiveScope();

            // FILE-scope issues: never auto-resolve via sweep
            if (scope == IssueScope.FILE) {
                results.add(new SweepResult(issue, false, "FILE-scope — skipped"));
                continue;
            }

            // Must have at least one reliable anchor
            if (!hasAnyReliableAnchor(issue)) {
                results.add(new SweepResult(issue, false, "No reliable anchor — skipped"));
                continue;
            }

            boolean contentFound = false;

            // Check codeSnippet — handle multi-line snippets
            if (issue.getCodeSnippet() != null && !issue.getCodeSnippet().isBlank()
                    && currentHashes.getLineCount() > 0) {

                Integer currentLine = issue.getLine();
                int hintLine = currentLine != null && currentLine > 0 ? currentLine : 1;

                String[] snippetLines = issue.getCodeSnippet().split("\\r?\\n");
                int matchedSnippetLines = 0;
                int totalNonBlankSnippetLines = 0;

                for (String snippetLine : snippetLines) {
                    if (snippetLine == null || snippetLine.isBlank()) continue;
                    totalNonBlankSnippetLines++;
                    String lineHash = LineHashSequence.hashLine(snippetLine);
                    int foundLine = currentHashes.findClosestLineForHash(lineHash, hintLine);
                    if (foundLine > 0) {
                        matchedSnippetLines++;
                    }
                }

                if (matchedSnippetLines > 0) {
                    // For BLOCK/FUNCTION scope, require ALL snippet lines gone to resolve
                    if ((scope == IssueScope.BLOCK || scope == IssueScope.FUNCTION)
                            && totalNonBlankSnippetLines > 1) {
                        // Some lines still present → not resolved
                        contentFound = true;
                    } else {
                        contentFound = true;
                    }
                }
            }

            // Check lineHash as fallback
            if (!contentFound && issue.getLineHash() != null
                    && currentHashes.getLineCount() > 0) {
                Integer currentLine = issue.getLine();
                int foundLine = currentHashes.findClosestLineForHash(
                        issue.getLineHash(),
                        currentLine != null && currentLine > 0 ? currentLine : 1);
                if (foundLine > 0) {
                    contentFound = true;
                }
            }

            if (!contentFound) {
                results.add(new SweepResult(issue, true,
                        "Code snippet/hash no longer found in file (deterministic sweep)"));
            } else {
                results.add(new SweepResult(issue, false, "Content anchor still present"));
            }
        }

        return results;
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  AST-aware overloads
    // ═══════════════════════════════════════════════════════════════════════

    /**
     * AST-aware version of {@link #classifyByContent(List, LineHashSequence)}.
     * <p>
     * When a parsed AST tree and scope resolver are available, context hash
     * comparison uses the <em>current</em> AST scope boundaries instead of
     * the stored {@code endLineNumber} / snippet heuristic. This is more accurate
     * because:
     * <ul>
     *   <li>The stored scope boundaries may be stale (from the detection-time AST)</li>
     *   <li>Functions may have grown/shrunk, changing scope boundaries</li>
     *   <li>The current scope at the anchor's new position may differ from the original</li>
     * </ul>
     *
     * @param issues        issues to classify (single file)
     * @param currentHashes line hashes for the current file content
     * @param parsedTree    AST-parsed tree for the current file (nullable — falls back to base method)
     * @param scopeResolver resolver to query scope at any line (nullable — falls back to base method)
     * @return per-issue classification
     */
    public List<ContentClassification> classifyByContent(
            List<? extends ReconcilableIssue> issues,
            LineHashSequence currentHashes,
            ParsedTree parsedTree,
            ScopeResolver scopeResolver) {

        // Fall back to base method if no AST available
        if (parsedTree == null || scopeResolver == null) {
            return classifyByContent(issues, currentHashes);
        }

        List<ContentClassification> results = new ArrayList<>();

        for (ReconcilableIssue issue : issues) {
            Integer currentLine = issue.getLine();
            IssueScope scope = issue.getEffectiveScope();

            // ── FILE-scope issues: always AI ──
            if (scope == IssueScope.FILE) {
                results.add(new ContentClassification(issue, Classification.NEEDS_AI, null, null));
                continue;
            }

            // ── Unanchored issues: always AI ──
            boolean hasNoReliableAnchor = (currentLine == null || currentLine <= 1)
                    && (issue.getCodeSnippet() == null || issue.getCodeSnippet().isBlank());
            if (hasNoReliableAnchor) {
                results.add(new ContentClassification(issue, Classification.NEEDS_AI, null, null));
                continue;
            }

            // Find the content anchor (same logic as base method)
            boolean contentFound = false;
            String updatedLineHash = null;
            int updatedLine = currentLine != null ? currentLine : 1;

            // 1st priority: codeSnippet
            if (issue.getCodeSnippet() != null && !issue.getCodeSnippet().isBlank()
                    && currentHashes.getLineCount() > 0) {

                String[] snippetLines = issue.getCodeSnippet().split("\\r?\\n");
                int bestFoundLine = -1;
                int bestDist = Integer.MAX_VALUE;
                int matchedSnippetLines = 0;
                int totalNonBlankSnippetLines = 0;

                for (String snippetLine : snippetLines) {
                    if (snippetLine == null || snippetLine.isBlank()) continue;
                    totalNonBlankSnippetLines++;
                    String lineHash = LineHashSequence.hashLine(snippetLine);
                    int foundLine = currentHashes.findClosestLineForHash(
                            lineHash, currentLine != null ? currentLine : 1);
                    if (foundLine > 0) {
                        matchedSnippetLines++;
                        int dist = Math.abs(foundLine - (currentLine != null ? currentLine : 1));
                        if (dist < bestDist) {
                            bestDist = dist;
                            bestFoundLine = foundLine;
                        }
                    }
                }

                if (bestFoundLine > 0) {
                    if ((scope == IssueScope.BLOCK || scope == IssueScope.FUNCTION)
                            && totalNonBlankSnippetLines > 1
                            && matchedSnippetLines < totalNonBlankSnippetLines) {
                        results.add(new ContentClassification(issue, Classification.NEEDS_AI, null, null));
                        continue;
                    }

                    updatedLine = bestFoundLine;
                    updatedLineHash = currentHashes.getHashForLine(bestFoundLine);
                    contentFound = true;
                }
            }

            // 2nd priority: lineHash
            if (!contentFound && issue.getLineHash() != null
                    && currentHashes.getLineCount() > 0) {
                int foundLine = currentHashes.findClosestLineForHash(
                        issue.getLineHash(), currentLine != null ? currentLine : 1);
                if (foundLine > 0) {
                    updatedLine = foundLine;
                    updatedLineHash = issue.getLineHash();
                    contentFound = true;
                }
            }

            if (contentFound) {
                // ── AST-driven context hash comparison ──
                // Resolve the CURRENT scope at the anchor's new position
                // instead of using the stored (potentially stale) endLineNumber.
                if (issue.getLineHashContext() != null
                        && (scope == IssueScope.BLOCK || scope == IssueScope.FUNCTION)) {
                    String currentContextHash = computeContextHashFromAst(
                            currentHashes, updatedLine, parsedTree, scopeResolver,
                            issue.getEndLineNumber(), issue.getCodeSnippet());
                    if (currentContextHash != null
                            && !currentContextHash.equals(issue.getLineHashContext())) {
                        log.debug("AST context hash changed for {} issue at line {} — " +
                                        "anchor intact but scope body modified, sending to AI.",
                                scope, updatedLine);
                        results.add(new ContentClassification(
                                issue, Classification.NEEDS_AI, updatedLine, updatedLineHash));
                        continue;
                    }
                }

                results.add(new ContentClassification(
                        issue, Classification.CONFIRMED, updatedLine, updatedLineHash));
            } else if (hasAnyReliableAnchor(issue)) {
                results.add(new ContentClassification(
                        issue, Classification.RESOLVED, null, null));
            } else {
                results.add(new ContentClassification(
                        issue, Classification.NEEDS_AI, null, null));
            }
        }

        return results;
    }

    /**
     * AST-aware version of {@link #sweepDeterministic(List, LineHashSequence)}.
     * <p>
     * Uses AST scope boundaries for more precise sweep decisions. When the anchor
     * line is found but the enclosing scope has been entirely rewritten, this can
     * detect that the issue's original context no longer exists.
     *
     * @param issues        issues to sweep (single file)
     * @param currentHashes line hashes for the current file content
     * @param parsedTree    AST-parsed tree for the current file (nullable — falls back)
     * @param scopeResolver resolver to query scope (nullable — falls back)
     * @return per-issue sweep result
     */
    public List<SweepResult> sweepDeterministic(
            List<? extends ReconcilableIssue> issues,
            LineHashSequence currentHashes,
            ParsedTree parsedTree,
            ScopeResolver scopeResolver) {

        // Fall back to base method if no AST available
        if (parsedTree == null || scopeResolver == null) {
            return sweepDeterministic(issues, currentHashes);
        }

        // The sweep logic is the same — AST doesn't change whether content is
        // present or absent. The main value of AST in sweep is for future
        // scope-boundary-aware resolution (e.g., detecting that the function
        // the issue was in no longer exists, even if the line hash matches
        // elsewhere in a different function).
        // For now, delegate to the base implementation.
        return sweepDeterministic(issues, currentHashes);
    }

    /**
     * AST-aware version of {@link #verifySnippetAnchors(List, LineHashSequence)}.
     * <p>
     * After finding the corrected line, resolves the current scope at that line
     * and uses AST boundaries for the context hash instead of the stored endLine.
     *
     * @param issues        issues to verify (single file)
     * @param lineHashes    line hashes for the current file content
     * @param parsedTree    AST-parsed tree for the current file (nullable — falls back)
     * @param scopeResolver resolver to query scope (nullable — falls back)
     * @return list of corrections
     */
    public List<SnippetVerificationResult> verifySnippetAnchors(
            List<? extends ReconcilableIssue> issues,
            LineHashSequence lineHashes,
            ParsedTree parsedTree,
            ScopeResolver scopeResolver) {

        if (parsedTree == null || scopeResolver == null) {
            return verifySnippetAnchors(issues, lineHashes);
        }

        if (issues.isEmpty() || lineHashes.getLineCount() == 0) {
            return List.of();
        }

        List<SnippetVerificationResult> results = new ArrayList<>();

        for (ReconcilableIssue issue : issues) {
            String snippet = issue.getCodeSnippet();
            if (snippet == null || snippet.isBlank()) continue;

            Integer currentLine = issue.getLine();
            if (currentLine == null || currentLine <= 0) continue;

            String[] snippetLines = snippet.split("\\r?\\n");
            boolean alreadyMatches = false;
            int bestFoundLine = -1;
            int bestDist = Integer.MAX_VALUE;

            for (String snippetLine : snippetLines) {
                if (snippetLine == null || snippetLine.isBlank()) continue;
                String lineHash = LineHashSequence.hashLine(snippetLine);

                if (!alreadyMatches && currentLine <= lineHashes.getLineCount()) {
                    String currentHash = lineHashes.getHashForLine(currentLine);
                    if (lineHash.equals(currentHash)) {
                        alreadyMatches = true;
                    }
                }

                int foundLine = lineHashes.findClosestLineForHash(lineHash, currentLine);
                if (foundLine > 0) {
                    int dist = Math.abs(foundLine - currentLine);
                    if (dist < bestDist) {
                        bestDist = dist;
                        bestFoundLine = foundLine;
                    }
                }
            }

            if (alreadyMatches) continue;

            if (bestFoundLine > 0 && bestFoundLine != currentLine) {
                // Use AST to compute context hash at the corrected location
                String contextHash = computeContextHashFromAst(
                        lineHashes, bestFoundLine, parsedTree, scopeResolver,
                        issue.getEndLineNumber(), issue.getCodeSnippet());
                results.add(new SnippetVerificationResult(
                        issue, bestFoundLine,
                        lineHashes.getHashForLine(bestFoundLine),
                        contextHash));
            }
        }

        return results;
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  Helpers
    // ═══════════════════════════════════════════════════════════════════════

    /**
     * Check if an issue has at least one reliable content anchor.
     */
    private boolean hasAnyReliableAnchor(ReconcilableIssue issue) {
        boolean hasSnippet = issue.getCodeSnippet() != null && !issue.getCodeSnippet().isBlank();
        boolean hasHash = issue.getLineHash() != null;
        return hasSnippet || hasHash;
    }

    /**
     * Compute the context hash for an issue based on its <em>actual</em> data.
     * <ol>
     *   <li>If {@code endLine} is known → hash the exact range
     *       {@code [lineNumber, endLine]} (covers the real function/block body).</li>
     *   <li>If only a {@code codeSnippet} is available → use the snippet's line
     *       count as the radius.</li>
     *   <li>Otherwise → radius of 1 (immediate neighbors for drift detection).</li>
     * </ol>
     *
     * Must produce the same hash as
     * {@code CodeAnalysisService.computeContextHash()} for the same inputs.
     */
    static String computeContextHash(
            LineHashSequence lineHashes,
            int lineNumber,
            Integer endLine,
            String codeSnippet
    ) {
        // 1. Exact range when scope boundaries are known
        if (endLine != null && endLine > lineNumber) {
            return lineHashes.getRangeHash(lineNumber, endLine);
        }

        // 2. Snippet-derived radius
        if (codeSnippet != null && !codeSnippet.isBlank()) {
            int snippetLineCount = codeSnippet.split("\\r?\\n").length;
            return lineHashes.getContextHash(lineNumber, Math.max(snippetLineCount, 1));
        }

        // 3. Minimal drift-detection window
        return lineHashes.getContextHash(lineNumber, 1);
    }

    /**
     * Compute context hash using the <b>current</b> AST scope boundaries at a given line.
     * Falls back to {@link #computeContextHash} if no scope is found.
     * <p>
     * Uses the same algorithm as {@link org.rostilos.codecrow.astparser.internal.DefaultScopeAwareHasher}:
     * a local context window bounded by scope boundaries (±20% of scope span, min 3 lines).
     */
    private static String computeContextHashFromAst(
            LineHashSequence lineHashes,
            int lineNumber,
            ParsedTree parsedTree,
            ScopeResolver scopeResolver,
            Integer fallbackEndLine,
            String fallbackSnippet) {

        try {
            Optional<ScopeInfo> scopeOpt = scopeResolver.innermostScopeAt(parsedTree, lineNumber);
            if (scopeOpt.isPresent()) {
                ScopeInfo scope = scopeOpt.get();
                // Compute a local context window within the scope (mirrors DefaultScopeAwareHasher)
                int scopeSpan = scope.lineSpan();
                int contextRadius = Math.max(3, scopeSpan / 5);
                int contextStart = Math.max(scope.startLine(), lineNumber - contextRadius);
                int contextEnd = Math.min(scope.endLine(), lineNumber + contextRadius);
                return lineHashes.getRangeHash(contextStart, contextEnd);
            }
        } catch (Exception e) {
            log.debug("AST scope resolution failed at line {}, falling back to heuristic: {}",
                    lineNumber, e.getMessage());
        }

        // Fallback: use the stored endLine / snippet heuristic
        return computeContextHash(lineHashes, lineNumber, fallbackEndLine, fallbackSnippet);
    }
}

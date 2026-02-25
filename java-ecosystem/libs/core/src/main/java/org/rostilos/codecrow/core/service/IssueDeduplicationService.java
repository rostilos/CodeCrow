package org.rostilos.codecrow.core.service;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.util.tracking.DiffSanitizer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.*;

/**
 * De-duplicates issues at ingestion time (before persistence) using a 3-tier strategy.
 * <p>
 * This mirrors and replaces the Python-side post-processing deduplication that was
 * implemented in {@code IssuePostProcessor._deduplicate_issues()}.
 * <p>
 * <h3>De-duplication tiers</h3>
 * <ol>
 *   <li><b>Structural key</b> — deterministic, exact match on
 *       {@code file:line:category}. Two issues at the same file, line, and category
 *       are always duplicates. Keeps the one with the highest severity and best
 *       suggested fix.</li>
 *   <li><b>Whole-file wildcard</b> — if an issue has line ≤ 1 (meaning the AI flagged
 *       the "whole file"), it absorbs any other issue in the same file+category.
 *       Conversely, a specific-line issue in file+category makes a subsequent
 *       whole-file entry redundant.</li>
 *   <li><b>Fingerprint-based</b> — two issues with the same
 *       {@link org.rostilos.codecrow.core.util.tracking.IssueFingerprint} value
 *       (same category + line-content hash + normalized title) are merged. This
 *       catches cases where the AI reports the same finding at slightly different
 *       line numbers after content-correction converges them to the same line hash.</li>
 * </ol>
 * <p>
 * When merging duplicates, the surviving issue keeps:
 * <ul>
 *   <li>The highest severity among the group</li>
 *   <li>The best (longest valid) suggested fix diff</li>
 *   <li>The lowest line number (most specific location)</li>
 * </ul>
 *
 * <h3>Thread safety</h3>
 * Instances are stateless beyond the logger. Safe to call from any thread.
 */
@Service
public class IssueDeduplicationService {

    private static final Logger log = LoggerFactory.getLogger(IssueDeduplicationService.class);

    /**
     * Severity ordering for comparison — higher value wins when merging duplicates.
     */
    private static final Map<IssueSeverity, Integer> SEVERITY_ORDER = Map.of(
            IssueSeverity.HIGH, 4,
            IssueSeverity.MEDIUM, 3,
            IssueSeverity.LOW, 2,
            IssueSeverity.INFO, 1
    );

    /**
     * De-duplicate a list of issues in-place (well, returns a new list).
     * <p>
     * Issues are grouped by file path and then passed through the three dedup tiers.
     * Resolved issues are never de-duplicated — they always pass through.
     *
     * @param issues the list of issues to de-duplicate; not modified
     * @return a new list with duplicates removed; order is preserved per file
     */
    public List<CodeAnalysisIssue> deduplicateAtIngestion(List<CodeAnalysisIssue> issues) {
        if (issues == null || issues.size() < 2) {
            return issues != null ? new ArrayList<>(issues) : new ArrayList<>();
        }

        // Partition by file path
        Map<String, List<CodeAnalysisIssue>> byFile = new LinkedHashMap<>();
        for (CodeAnalysisIssue issue : issues) {
            String path = issue.getFilePath() != null ? issue.getFilePath() : "unknown";
            byFile.computeIfAbsent(path, k -> new ArrayList<>()).add(issue);
        }

        List<CodeAnalysisIssue> result = new ArrayList<>(issues.size());
        int totalRemoved = 0;

        for (Map.Entry<String, List<CodeAnalysisIssue>> entry : byFile.entrySet()) {
            String filePath = entry.getKey();
            List<CodeAnalysisIssue> fileIssues = entry.getValue();

            if (fileIssues.size() < 2) {
                result.addAll(fileIssues);
                continue;
            }

            // Separate resolved issues — never dedup those
            List<CodeAnalysisIssue> active = new ArrayList<>();
            List<CodeAnalysisIssue> resolved = new ArrayList<>();
            for (CodeAnalysisIssue issue : fileIssues) {
                if (issue.isResolved()) {
                    resolved.add(issue);
                } else {
                    active.add(issue);
                }
            }

            int before = active.size();

            // Tier 1 + 2: structural key + whole-file wildcard (single pass)
            active = structuralDedup(active, filePath);

            // Tier 3: fingerprint-based
            active = fingerprintDedup(active, filePath);

            int removed = before - active.size();
            totalRemoved += removed;

            if (removed > 0) {
                log.info("Dedup removed {} duplicate(s) in {} (kept {})", removed, filePath, active.size());
            }

            result.addAll(active);
            result.addAll(resolved);
        }

        if (totalRemoved > 0) {
            log.info("Ingestion dedup: removed {} total duplicates across {} files ({} → {} issues)",
                    totalRemoved, byFile.size(), issues.size(), result.size());
        }

        return result;
    }

    // ── Tier 1 + 2: Structural key + whole-file wildcard ─────────────────

    /**
     * Single-pass structural de-duplication per file.
     * <p>
     * Tier 1: exact {@code file:line:category} match → keep best.<br>
     * Tier 2: whole-file wildcard (line ≤ 1) absorbs same file+category.
     */
    private List<CodeAnalysisIssue> structuralDedup(List<CodeAnalysisIssue> issues, String filePath) {
        // content-key → best issue so far
        Map<String, CodeAnalysisIssue> byContentKey = new LinkedHashMap<>();
        // file:category keys we've seen (any line)
        Set<String> seenFileCat = new HashSet<>();
        // file:category keys where line ≤ 1 (whole-file wildcard)
        Set<String> seenWholeFileCat = new HashSet<>();

        int dupCount = 0;

        for (CodeAnalysisIssue issue : issues) {
            String cat = issue.getIssueCategory() != null ? issue.getIssueCategory().name() : "UNKNOWN";
            int line = issue.getLineNumber() != null ? issue.getLineNumber() : 0;
            boolean wholeFile = line <= 1;

            String contentKey = filePath + ":" + line + ":" + cat;
            String fileCatKey = filePath + ":" + cat;

            // Tier 2: whole-file wildcard already covers this specific-line issue
            if (!wholeFile && seenWholeFileCat.contains(fileCatKey)) {
                dupCount++;
                // Still merge severity upward if this specific-line issue is more severe
                mergeIntoExistingBestByFileCat(byContentKey, fileCatKey, issue);
                continue;
            }

            // Tier 2: this is a whole-file entry but a specific-line issue already exists
            if (wholeFile && seenFileCat.contains(fileCatKey) && !seenWholeFileCat.contains(fileCatKey)) {
                dupCount++;
                continue;
            }

            // Tier 1: exact content key duplicate
            if (byContentKey.containsKey(contentKey)) {
                CodeAnalysisIssue existing = byContentKey.get(contentKey);
                byContentKey.put(contentKey, pickBest(existing, issue));
                dupCount++;
                continue;
            }

            byContentKey.put(contentKey, issue);
            seenFileCat.add(fileCatKey);
            if (wholeFile) {
                seenWholeFileCat.add(fileCatKey);
            }
        }

        if (dupCount > 0) {
            log.debug("Structural dedup: removed {} in {}", dupCount, filePath);
        }

        return new ArrayList<>(byContentKey.values());
    }

    /**
     * When a whole-file entry already won, but a specific-line duplicate arrives
     * with a higher severity — promote the surviving whole-file issue.
     */
    private void mergeIntoExistingBestByFileCat(
            Map<String, CodeAnalysisIssue> byContentKey,
            String fileCatKey,
            CodeAnalysisIssue challenger
    ) {
        // Find the existing whole-file entry by iterating (small map per file)
        for (Map.Entry<String, CodeAnalysisIssue> e : byContentKey.entrySet()) {
            if (e.getKey().startsWith(fileCatKey.substring(0, fileCatKey.lastIndexOf(':') + 1))) {
                String cat = fileCatKey.substring(fileCatKey.lastIndexOf(':') + 1);
                CodeAnalysisIssue existing = e.getValue();
                String existingCat = existing.getIssueCategory() != null ? existing.getIssueCategory().name() : "UNKNOWN";
                if (existingCat.equals(cat)) {
                    e.setValue(pickBest(existing, challenger));
                    return;
                }
            }
        }
    }

    // ── Tier 3: Fingerprint-based ────────────────────────────────────────

    /**
     * De-duplicate by issue fingerprint — catches issues at different lines
     * that converge to the same content hash + category + normalized title.
     */
    private List<CodeAnalysisIssue> fingerprintDedup(List<CodeAnalysisIssue> issues, String filePath) {
        if (issues.size() < 2) {
            return issues;
        }

        Map<String, CodeAnalysisIssue> byFingerprint = new LinkedHashMap<>();
        int dupCount = 0;

        for (CodeAnalysisIssue issue : issues) {
            String fp = issue.getIssueFingerprint();
            if (fp == null || fp.isBlank()) {
                // No fingerprint — can't dedup by it, keep the issue
                byFingerprint.put("__no_fp_" + dupCount + "_" + System.nanoTime(), issue);
                continue;
            }

            if (byFingerprint.containsKey(fp)) {
                CodeAnalysisIssue existing = byFingerprint.get(fp);
                byFingerprint.put(fp, pickBest(existing, issue));
                dupCount++;
            } else {
                byFingerprint.put(fp, issue);
            }
        }

        if (dupCount > 0) {
            log.debug("Fingerprint dedup: removed {} in {}", dupCount, filePath);
        }

        return new ArrayList<>(byFingerprint.values());
    }

    // ── Merge helpers ────────────────────────────────────────────────────

    /**
     * Pick the "best" of two duplicate issues:
     * <ol>
     *   <li>Higher severity wins</li>
     *   <li>If tied, the one with the longer valid diff wins</li>
     *   <li>If still tied, the one with the lower (more specific) line number wins</li>
     * </ol>
     */
    /**
     * Tier 4: category-agnostic content fingerprint dedup.
     * Catches the same issue classified under different categories (e.g. STYLE vs CODE_QUALITY)
     * by using only lineHash + normalizedTitle.
     */
    private List<CodeAnalysisIssue> contentFingerprintDedup(List<CodeAnalysisIssue> issues, String filePath) {
        if (issues.size() < 2) {
            return issues;
        }

        Map<String, CodeAnalysisIssue> byContentFp = new LinkedHashMap<>();
        int dupCount = 0;

        for (CodeAnalysisIssue issue : issues) {
            String cfp = issue.getContentFingerprint();
            if (cfp == null || cfp.isBlank()) {
                byContentFp.put("__no_cfp_" + dupCount + "_" + System.nanoTime(), issue);
                continue;
            }

            if (byContentFp.containsKey(cfp)) {
                CodeAnalysisIssue existing = byContentFp.get(cfp);
                byContentFp.put(cfp, pickBest(existing, issue));
                dupCount++;
            } else {
                byContentFp.put(cfp, issue);
            }
        }

        if (dupCount > 0) {
            log.debug("Content-fingerprint dedup: removed {} in {}", dupCount, filePath);
        }

        return new ArrayList<>(byContentFp.values());
    }

    private CodeAnalysisIssue pickBest(CodeAnalysisIssue a, CodeAnalysisIssue b) {
        int sevA = severityRank(a.getSeverity());
        int sevB = severityRank(b.getSeverity());
        if (sevA != sevB) {
            return sevA >= sevB ? promoteSeverity(a, b) : promoteSeverity(b, a);
        }

        int diffLenA = validDiffLength(a.getSuggestedFixDiff());
        int diffLenB = validDiffLength(b.getSuggestedFixDiff());
        if (diffLenA != diffLenB) {
            return diffLenA >= diffLenB ? a : b;
        }

        int lineA = a.getLineNumber() != null ? a.getLineNumber() : Integer.MAX_VALUE;
        int lineB = b.getLineNumber() != null ? b.getLineNumber() : Integer.MAX_VALUE;
        return lineA <= lineB ? a : b;
    }

    /**
     * Return the winner but ensure it carries the highest severity from either issue.
     */
    private CodeAnalysisIssue promoteSeverity(CodeAnalysisIssue winner, CodeAnalysisIssue loser) {
        // Winner already has higher severity, but adopt loser's diff if winner lacks one
        if (validDiffLength(winner.getSuggestedFixDiff()) == 0
                && validDiffLength(loser.getSuggestedFixDiff()) > 0) {
            winner.setSuggestedFixDiff(loser.getSuggestedFixDiff());
            winner.setSuggestedFixDescription(loser.getSuggestedFixDescription());
        }
        return winner;
    }

    private int severityRank(IssueSeverity severity) {
        return severity != null ? SEVERITY_ORDER.getOrDefault(severity, 0) : 0;
    }

    private int validDiffLength(String diff) {
        if (diff == null || !DiffSanitizer.isValidDiffFormat(diff)) {
            return 0;
        }
        return diff.length();
    }
}

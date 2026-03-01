package org.rostilos.codecrow.analysisengine.service.pr;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.core.util.tracking.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.*;
import java.util.stream.Collectors;

/**
 * Applies deterministic 4-pass issue tracking ({@link IssueTracker}) to PR iterations.
 * <p>
 * When a PR is updated (new push), the AI produces a fresh set of issues. This service
 * matches the new issues against the previous iteration's issues using content-based
 * fingerprints and line hashes, then records the tracking lineage on each new issue.
 * <p>
 * This provides:
 * <ul>
 *   <li>Stable issue identity across PR iterations (VCS comments don't duplicate)</li>
 *   <li>Clear "new / persisting / resolved" classification for the source code viewer</li>
 *   <li>Audit trail via {@code trackedFromIssueId} + {@code trackingConfidence}</li>
 * </ul>
 */
@Service
@Transactional
public class PrIssueTrackingService {

    private static final Logger log = LoggerFactory.getLogger(PrIssueTrackingService.class);

    private final CodeAnalysisIssueRepository issueRepository;

    public PrIssueTrackingService(CodeAnalysisIssueRepository issueRepository) {
        this.issueRepository = issueRepository;
    }

    /**
     * Run deterministic tracking between a new analysis and the previous analysis for the same PR.
     * <p>
     * For each matched pair:
     * <ul>
     *   <li>{@code trackedFromIssueId} is set to the previous issue's ID</li>
     *   <li>{@code trackingConfidence} is set to the match confidence (EXACT, SHIFTED, etc.)</li>
     * </ul>
     * Unmatched new issues are genuinely new (first time in this PR).
     * Unmatched previous issues are resolved (no longer flagged).
     *
     * @param newAnalysis      the newly created analysis (issues already persisted, with fingerprints)
     * @param previousAnalysis the most recent previous analysis for the same PR, or null
     * @param newFileContents  file contents for the new analysis commit (for re-computing hashes)
     * @param prevFileContents file contents for the previous analysis commit (for re-computing hashes)
     * @return a tracking summary
     */
    public TrackingSummary trackPrIteration(
            CodeAnalysis newAnalysis,
            CodeAnalysis previousAnalysis,
            Map<String, String> newFileContents,
            Map<String, String> prevFileContents
    ) {
        if (previousAnalysis == null) {
            log.info("No previous analysis for PR — first iteration, skipping tracking for analysis {}",
                    newAnalysis.getId());
            return new TrackingSummary(0, 0, newAnalysis.getIssues().size(), 0);
        }

        List<CodeAnalysisIssue> newIssues = newAnalysis.getIssues();
        List<CodeAnalysisIssue> prevIssues = previousAnalysis.getIssues();

        if (newIssues.isEmpty() && prevIssues.isEmpty()) {
            return new TrackingSummary(0, 0, 0, 0);
        }

        // Group issues by file for per-file tracking (same approach as BranchAnalysisProcessor)
        Map<String, List<CodeAnalysisIssue>> newByFile = groupByFile(newIssues);
        Map<String, List<CodeAnalysisIssue>> prevByFile = groupByFile(prevIssues);

        // Collect all file paths involved
        Set<String> allFiles = new HashSet<>();
        allFiles.addAll(newByFile.keySet());
        allFiles.addAll(prevByFile.keySet());

        int matched = 0;
        int newOnly = 0;
        int resolved = 0;

        for (String filePath : allFiles) {
            List<CodeAnalysisIssue> fileNewIssues = newByFile.getOrDefault(filePath, List.of());
            List<CodeAnalysisIssue> filePrevIssues = prevByFile.getOrDefault(filePath, List.of());

            if (fileNewIssues.isEmpty()) {
                // All previous issues in this file are resolved
                resolved += filePrevIssues.size();
                continue;
            }
            if (filePrevIssues.isEmpty()) {
                // All new issues in this file are genuinely new
                newOnly += fileNewIssues.size();
                continue;
            }

            // Wrap issues as Trackables
            // RAW = new issues (recomputed against new file content)
            List<TrackableIssue> rawTrackables = fileNewIssues.stream()
                    .map(issue -> recomputeTrackable(issue, newFileContents))
                    .collect(Collectors.toList());

            // BASE = previous issues (with original detection-time hashes)
            List<TrackableIssue> baseTrackables = filePrevIssues.stream()
                    .map(TrackableIssue::fromOriginal)
                    .collect(Collectors.toList());

            // Run 4-pass tracking
            Tracking<TrackableIssue, TrackableIssue> tracking =
                    IssueTracker.track(rawTrackables, baseTrackables);

            // Process matched pairs — record lineage + carry forward resolution status
            for (Tracking.MatchedPair<TrackableIssue, TrackableIssue> pair : tracking.getMatchedPairs()) {
                CodeAnalysisIssue newIssue = pair.raw().issue();
                CodeAnalysisIssue prevIssue = pair.base().issue();

                newIssue.setTrackedFromIssueId(prevIssue.getId());
                newIssue.setTrackingConfidence(pair.confidence().name());

                // If the previous issue was resolved (e.g. user dismissed it), carry that
                // status forward so the same issue doesn't reappear as an annotation.
                if (prevIssue.isResolved()) {
                    newIssue.setResolved(true);
                    newIssue.setResolvedDescription(prevIssue.getResolvedDescription());
                    newIssue.setResolvedByPr(prevIssue.getResolvedByPr());
                    newIssue.setResolvedCommitHash(prevIssue.getResolvedCommitHash());
                    newIssue.setResolvedAnalysisId(prevIssue.getResolvedAnalysisId());
                    newIssue.setResolvedAt(prevIssue.getResolvedAt());
                    newIssue.setResolvedBy(prevIssue.getResolvedBy());
                    log.info("Carried forward resolved status from issue {} to new issue {} (confidence={})",
                            prevIssue.getId(), newIssue.getId(), pair.confidence().name());
                }

                issueRepository.save(newIssue);
                matched++;
            }

            // Unmatched new = genuinely new issues (no lineage to set)
            newOnly += tracking.getUnmatchedRaws().size();

            // Unmatched previous = issues resolved in this iteration
            resolved += tracking.getUnmatchedBases().size();
        }

        log.info("PR tracking for analysis {}: {} matched, {} new, {} resolved (previous analysis={})",
                newAnalysis.getId(), matched, newOnly, resolved, previousAnalysis.getId());

        return new TrackingSummary(matched, resolved, newOnly,
                prevIssues.stream().filter(CodeAnalysisIssue::isResolved).count());
    }

    // ── Trackable adapter ────────────────────────────────────────────────

    /**
     * Wraps a {@link CodeAnalysisIssue} as a {@link Trackable} for the tracker.
     * Can use either the original stored hashes or recomputed hashes from fresh file content.
     */
    private record TrackableIssue(
            CodeAnalysisIssue issue,
            String fingerprint,
            Integer line,
            String lineHash,
            String filePath
    ) implements Trackable {

        @Override public String getIssueFingerprint() { return fingerprint; }
        @Override public Integer getLine() { return line; }
        @Override public String getLineHash() { return lineHash; }
        @Override public String getFilePath() { return filePath; }

        /**
         * Create from an issue using its original stored hashes (for the base/previous side).
         */
        static TrackableIssue fromOriginal(CodeAnalysisIssue issue) {
            return new TrackableIssue(
                    issue,
                    issue.getIssueFingerprint(),
                    issue.getLineNumber(),
                    issue.getLineHash(),
                    issue.getFilePath()
            );
        }
    }

    /**
     * Create a Trackable for a new issue, recomputing line hash from fresh file content.
     * The fingerprint stays the same (it was computed during issue creation), but
     * lineHash is recomputed against the current file content for accurate matching.
     */
    private TrackableIssue recomputeTrackable(CodeAnalysisIssue issue, Map<String, String> fileContents) {
        String lineHash = issue.getLineHash();
        Integer line = issue.getLineNumber();

        // If we have file content, recompute to ensure accuracy
        if (fileContents != null && issue.getFilePath() != null
                && fileContents.containsKey(issue.getFilePath())) {
            LineHashSequence hashes = LineHashSequence.from(fileContents.get(issue.getFilePath()));
            if (line != null && line > 0 && hashes.getLineCount() > 0) {
                lineHash = hashes.getHashForLine(line);
            }
        }

        return new TrackableIssue(
                issue,
                issue.getIssueFingerprint(),
                line,
                lineHash,
                issue.getFilePath()
        );
    }

    private Map<String, List<CodeAnalysisIssue>> groupByFile(List<CodeAnalysisIssue> issues) {
        return issues.stream()
                .filter(i -> i.getFilePath() != null)
                .collect(Collectors.groupingBy(CodeAnalysisIssue::getFilePath));
    }

    // ── Summary DTO ──────────────────────────────────────────────────────

    /**
     * Summary of the tracking result for logging and event publishing.
     */
    public record TrackingSummary(
            int matchedCount,
            int resolvedCount,
            int newIssueCount,
            long previouslyResolvedCount
    ) {
        public boolean isFirstIteration() {
            return matchedCount == 0 && resolvedCount == 0 && previouslyResolvedCount == 0;
        }
    }
}

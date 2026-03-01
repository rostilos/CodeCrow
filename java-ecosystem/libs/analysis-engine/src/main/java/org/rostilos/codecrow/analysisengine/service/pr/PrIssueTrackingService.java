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
        int unanchoredResolved = 0;
        int unanchoredPersisting = 0;

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

            // ── Separate unanchored previous issues (line <= 1, no lineHash, no codeSnippet) ──
            // These cannot be reliably tracked by content hashing. Instead, match them
            // by the original issue ID that the AI preserves in its response.
            // If the AI omitted the issue entirely → resolved (AI didn't find it).
            // If the AI re-reported it with isResolved=true → resolved.
            // If the AI re-reported it with isResolved=false → mark for AI reconciliation.
            List<CodeAnalysisIssue> anchoredPrevIssues = new ArrayList<>();
            List<CodeAnalysisIssue> unanchoredPrevIssues = new ArrayList<>();
            for (CodeAnalysisIssue prev : filePrevIssues) {
                if (isUnanchored(prev)) {
                    unanchoredPrevIssues.add(prev);
                } else {
                    anchoredPrevIssues.add(prev);
                }
            }

            // Handle unanchored previous issues via fingerprint matching.
            // Unlike IssueTracker (which would make these "immortal" via Pass 3/4),
            // we match by fingerprint but ALSO respect the new issue's isResolved flag
            // from the AI's Stage 1 review.
            if (!unanchoredPrevIssues.isEmpty()) {
                // Build a lookup: fingerprint → list of new issues with that fingerprint
                Map<String, List<CodeAnalysisIssue>> newByFingerprint = new LinkedHashMap<>();
                for (CodeAnalysisIssue newIssue : fileNewIssues) {
                    String fp = newIssue.getIssueFingerprint();
                    if (fp != null) {
                        newByFingerprint.computeIfAbsent(fp, k -> new ArrayList<>()).add(newIssue);
                    }
                }

                Set<CodeAnalysisIssue> unanchoredMatchedNewIssues = new HashSet<>();
                for (CodeAnalysisIssue prevIssue : unanchoredPrevIssues) {
                    String prevFp = prevIssue.getIssueFingerprint();
                    List<CodeAnalysisIssue> candidates = prevFp != null
                            ? newByFingerprint.getOrDefault(prevFp, List.of())
                            : List.of();

                    // Pick the first unmatched candidate (unanchored issues at line 1 are
                    // identical in terms of fingerprint, so order doesn't matter)
                    CodeAnalysisIssue matchedNew = null;
                    for (CodeAnalysisIssue c : candidates) {
                        if (!unanchoredMatchedNewIssues.contains(c)) {
                            matchedNew = c;
                            break;
                        }
                    }

                    if (matchedNew != null) {
                        // AI re-reported this issue — link them
                        matchedNew.setTrackedFromIssueId(prevIssue.getId());
                        matchedNew.setTrackingConfidence("UNANCHORED_FP_MATCH");
                        unanchoredMatchedNewIssues.add(matchedNew);

                        if (matchedNew.isResolved()) {
                            // AI marked it resolved in Stage 1 → trust it
                            unanchoredResolved++;
                            log.info("Unanchored issue {} resolved by AI (new issue {}, file={})",
                                    prevIssue.getId(), matchedNew.getId(), filePath);
                        } else if (prevIssue.isResolved()) {
                            // Previous was resolved (user dismissed) → carry forward
                            matchedNew.setResolved(true);
                            matchedNew.setResolvedDescription(prevIssue.getResolvedDescription());
                            matchedNew.setResolvedByPr(prevIssue.getResolvedByPr());
                            matchedNew.setResolvedCommitHash(prevIssue.getResolvedCommitHash());
                            matchedNew.setResolvedAnalysisId(prevIssue.getResolvedAnalysisId());
                            matchedNew.setResolvedAt(prevIssue.getResolvedAt());
                            matchedNew.setResolvedBy(prevIssue.getResolvedBy());
                            unanchoredResolved++;
                        } else {
                            // AI says still present — persists (but can be overridden by
                            // dedicated AI reconciliation if caller implements it)
                            unanchoredPersisting++;
                        }
                        issueRepository.save(matchedNew);
                        matched++;
                    } else {
                        // AI did NOT re-report this issue → it's resolved
                        unanchoredResolved++;
                        resolved++;
                        log.info("Unanchored issue {} resolved — AI omitted it in new review (file={})",
                                prevIssue.getId(), filePath);
                    }
                }

                // Remove unanchored-matched new issues from the pool before IssueTracker
                // so they don't get double-counted or double-matched
                fileNewIssues = fileNewIssues.stream()
                        .filter(ni -> !unanchoredMatchedNewIssues.contains(ni))
                        .collect(Collectors.toList());
            }

            // ── Run IssueTracker on anchored issues only ──
            if (anchoredPrevIssues.isEmpty() && fileNewIssues.isEmpty()) {
                continue;
            }
            if (anchoredPrevIssues.isEmpty()) {
                newOnly += fileNewIssues.size();
                continue;
            }
            if (fileNewIssues.isEmpty()) {
                resolved += anchoredPrevIssues.size();
                continue;
            }

            // Wrap issues as Trackables
            // RAW = new issues (recomputed against new file content)
            List<TrackableIssue> rawTrackables = fileNewIssues.stream()
                    .map(issue -> recomputeTrackable(issue, newFileContents))
                    .collect(Collectors.toList());

            // BASE = previous issues (with original detection-time hashes)
            List<TrackableIssue> baseTrackables = anchoredPrevIssues.stream()
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

                // Check resolved status from BOTH directions:
                // 1. Previous issue was resolved (user dismissed, or previous reconciliation)
                // 2. New issue was marked resolved by the AI in Stage 1 review
                //    (createIssueFromData sets isResolved=true when AI says isResolved: true)
                if (prevIssue.isResolved()) {
                    // Carry forward previous resolution
                    newIssue.setResolved(true);
                    newIssue.setResolvedDescription(prevIssue.getResolvedDescription());
                    newIssue.setResolvedByPr(prevIssue.getResolvedByPr());
                    newIssue.setResolvedCommitHash(prevIssue.getResolvedCommitHash());
                    newIssue.setResolvedAnalysisId(prevIssue.getResolvedAnalysisId());
                    newIssue.setResolvedAt(prevIssue.getResolvedAt());
                    newIssue.setResolvedBy(prevIssue.getResolvedBy());
                    log.info("Carried forward resolved status from issue {} to new issue {} (confidence={})",
                            prevIssue.getId(), newIssue.getId(), pair.confidence().name());
                } else if (newIssue.isResolved()) {
                    // AI in Stage 1 marked this re-emitted issue as resolved — trust it
                    log.info("AI marked matched issue as resolved: prev={} → new={} (confidence={})",
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

        log.info("PR tracking for analysis {}: {} matched, {} new, {} resolved " +
                        "(unanchored: {} resolved, {} persisting) (previous analysis={})",
                newAnalysis.getId(), matched, newOnly, resolved,
                unanchoredResolved, unanchoredPersisting, previousAnalysis.getId());

        return new TrackingSummary(matched, resolved, newOnly,
                prevIssues.stream().filter(CodeAnalysisIssue::isResolved).count(),
                unanchoredResolved, unanchoredPersisting);
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

    /**
     * An issue is "unanchored" when it has no meaningful code location —
     * line is absent or 1, no line hash, and no code snippet.
     * These cannot be reliably tracked by content hashing (IssueTracker Pass 3/4
     * would match them forever on fingerprint+line alone).
     */
    private boolean isUnanchored(CodeAnalysisIssue issue) {
        return (issue.getLineNumber() == null || issue.getLineNumber() <= 1)
                && issue.getLineHash() == null
                && (issue.getCodeSnippet() == null || issue.getCodeSnippet().isBlank());
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
            long previouslyResolvedCount,
            int unanchoredResolvedCount,
            int unanchoredPersistingCount
    ) {
        /** Convenience constructor for first iteration (no tracking). */
        public TrackingSummary(int matchedCount, int resolvedCount, int newIssueCount, long previouslyResolvedCount) {
            this(matchedCount, resolvedCount, newIssueCount, previouslyResolvedCount, 0, 0);
        }

        public boolean isFirstIteration() {
            return matchedCount == 0 && resolvedCount == 0 && previouslyResolvedCount == 0;
        }
    }
}

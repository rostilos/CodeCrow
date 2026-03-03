package org.rostilos.codecrow.commitgraph.service;

import org.rostilos.codecrow.commitgraph.persistence.AnalyzedCommitRepository;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.model.pullrequest.PullRequestState;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.*;
import java.util.stream.Collectors;

/**
 * Determines whether unanalyzed commits on a branch are already covered
 * by pull requests (open or recently merged) targeting that branch.
 * <p>
 * Uses the {@code analyzed_commit} table for efficient set-based lookups
 * instead of the old DAG-based approach.
 * <p>
 * Used by {@code BranchAnalysisProcessor} to decide whether to run
 * a full AI analysis (hybrid path) or only reconciliation.
 */
@Service
public class CommitCoverageService {

    private static final Logger log = LoggerFactory.getLogger(CommitCoverageService.class);

    private final PullRequestRepository pullRequestRepository;
    private final CodeAnalysisRepository codeAnalysisRepository;
    private final AnalyzedCommitRepository analyzedCommitRepository;

    public CommitCoverageService(PullRequestRepository pullRequestRepository,
                                 CodeAnalysisRepository codeAnalysisRepository,
                                 AnalyzedCommitRepository analyzedCommitRepository) {
        this.pullRequestRepository = pullRequestRepository;
        this.codeAnalysisRepository = codeAnalysisRepository;
        this.analyzedCommitRepository = analyzedCommitRepository;
    }

    /**
     * Result of the coverage check.
     *
     * @param status            overall coverage status
     * @param uncoveredCommits  commit hashes that are NOT covered by any open PR analysis
     *                          (empty when FULLY_COVERED, same as input when NOT_COVERED)
     */
    public record CoverageResult(CoverageStatus status, List<String> uncoveredCommits) {

        public boolean requiresAnalysis() {
            return status != CoverageStatus.FULLY_COVERED;
        }
    }

    public enum CoverageStatus {
        /** All unanalyzed commits are covered by at least one open PR's analyzed commit range. */
        FULLY_COVERED,
        /** Some (but not all) unanalyzed commits are covered. */
        PARTIALLY_COVERED,
        /** No unanalyzed commits are covered by any open PR. */
        NOT_COVERED
    }

    /**
     * Check if the given unanalyzed commits are covered by PRs targeting the branch.
     * <p>
     * Uses a two-tier approach:
     * <ol>
     *   <li>Check the {@code analyzed_commit} table (fast set lookup)</li>
     *   <li>For remaining commits, check open/merged PR analyses</li>
     * </ol>
     *
     * @param projectId          the project ID
     * @param targetBranchName   the branch being pushed to
     * @param unanalyzedCommits  the commit hashes that need analysis (oldest first)
     * @return coverage result with status and uncovered commits
     */
    public CoverageResult checkCoverage(Long projectId, String targetBranchName,
                                         List<String> unanalyzedCommits) {
        if (unanalyzedCommits == null || unanalyzedCommits.isEmpty()) {
            return new CoverageResult(CoverageStatus.FULLY_COVERED, Collections.emptyList());
        }

        // Tier 1: Check the analyzed_commit table directly (covers both branch & PR analyses)
        Set<String> alreadyRecorded = analyzedCommitRepository
                .findAnalyzedHashesByProjectIdAndCommitHashIn(projectId, unanalyzedCommits);

        List<String> notInTable = unanalyzedCommits.stream()
                .filter(h -> !alreadyRecorded.contains(h))
                .collect(Collectors.toList());

        if (notInTable.isEmpty()) {
            log.info("All {} unanalyzed commits already recorded in analyzed_commit table",
                    unanalyzedCommits.size());
            return new CoverageResult(CoverageStatus.FULLY_COVERED, Collections.emptyList());
        }

        // Tier 2: Check PRs targeting this branch (open + merged)
        List<PullRequest> relevantPRs = pullRequestRepository.findByProjectIdAndTargetBranchNameAndStateIn(
                projectId, targetBranchName,
                List.of(PullRequestState.OPEN, PullRequestState.MERGED));

        if (relevantPRs.isEmpty()) {
            log.debug("No open/merged PRs targeting branch {} — {} commits uncovered",
                    targetBranchName, notInTable.size());
            return buildResult(unanalyzedCommits, notInTable);
        }

        // Collect commit hashes that have been analyzed via PR for this project
        Set<String> prAnalyzedCommits = new HashSet<>();
        for (PullRequest pr : relevantPRs) {
            if (pr.getCommitHash() != null) {
                codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(
                        projectId, pr.getCommitHash(), pr.getPrNumber()
                ).ifPresent(analysis -> {
                    prAnalyzedCommits.add(pr.getCommitHash());
                    log.debug("PR #{} (state={}) has analysis for commit {} on target branch {}",
                            pr.getPrNumber(), pr.getState(),
                            shortHash(pr.getCommitHash()), targetBranchName);
                });
            }
        }

        // Filter out PR-covered commits
        List<String> uncovered = notInTable.stream()
                .filter(hash -> !prAnalyzedCommits.contains(hash))
                .collect(Collectors.toList());

        return buildResult(unanalyzedCommits, uncovered);
    }

    private CoverageResult buildResult(List<String> original, List<String> uncovered) {
        if (uncovered.isEmpty()) {
            return new CoverageResult(CoverageStatus.FULLY_COVERED, Collections.emptyList());
        } else if (uncovered.size() < original.size()) {
            log.info("{}/{} unanalyzed commits covered — {} uncovered",
                    original.size() - uncovered.size(), original.size(), uncovered.size());
            return new CoverageResult(CoverageStatus.PARTIALLY_COVERED, uncovered);
        } else {
            return new CoverageResult(CoverageStatus.NOT_COVERED, new ArrayList<>(uncovered));
        }
    }

    private static String shortHash(String hash) {
        return hash != null ? hash.substring(0, Math.min(7, hash.length())) : "null";
    }
}

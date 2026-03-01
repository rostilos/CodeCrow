package org.rostilos.codecrow.analysisengine.service.gitgraph;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.gitgraph.CommitNode;
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
 * Used by {@code BranchAnalysisProcessor} to decide whether to run
 * a full AI analysis (hybrid path) or only reconciliation.
 */
@Service
public class CommitCoverageService {

    private static final Logger log = LoggerFactory.getLogger(CommitCoverageService.class);

    private final PullRequestRepository pullRequestRepository;
    private final CodeAnalysisRepository codeAnalysisRepository;

    public CommitCoverageService(PullRequestRepository pullRequestRepository,
                                 CodeAnalysisRepository codeAnalysisRepository) {
        this.pullRequestRepository = pullRequestRepository;
        this.codeAnalysisRepository = codeAnalysisRepository;
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
     * Checks both OPEN and MERGED PRs.  MERGED PRs must be included because
     * the branch analysis flow marks a PR as MERGED <em>before</em> calling this
     * method, so a freshly-merged PR would otherwise be invisible.
     * <p>
     * A commit is considered "covered" if it was already analyzed as part of a PR analysis
     * (i.e., there exists a {@code CodeAnalysis} with {@code analysisType = PR_REVIEW}
     * whose commit hash matches).
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

        // Find PRs targeting this branch — both OPEN and MERGED.
        // MERGED PRs must be included because:
        //   1. The branch analysis marks the PR as MERGED *before* calling this check
        //   2. Merge commits have different hashes from the PR's source commits,
        //      so we need the PR's analyzed commit hash for coverage matching
        List<PullRequest> relevantPRs = pullRequestRepository.findByProjectIdAndTargetBranchNameAndStateIn(
                projectId, targetBranchName,
                List.of(PullRequestState.OPEN, PullRequestState.MERGED));

        if (relevantPRs.isEmpty()) {
            log.debug("No open/merged PRs targeting branch {} — all {} commits are uncovered",
                    targetBranchName, unanalyzedCommits.size());
            return new CoverageResult(CoverageStatus.NOT_COVERED, new ArrayList<>(unanalyzedCommits));
        }

        // Collect all commit hashes that have been analyzed via PR_REVIEW for this project
        Set<String> prAnalyzedCommits = new HashSet<>();
        for (PullRequest pr : relevantPRs) {
            // Each PR's latest commit hash was analyzed when the PR was reviewed
            if (pr.getCommitHash() != null) {
                // Check if there's an actual analysis for this PR's commit
                codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(
                        projectId, pr.getCommitHash(), pr.getPrNumber()
                ).ifPresent(analysis -> {
                    // The PR analysis covers this commit and potentially all commits
                    // in the PR's source branch up to this point
                    prAnalyzedCommits.add(pr.getCommitHash());
                    log.debug("PR #{} (state={}) has analysis for commit {} on target branch {}",
                            pr.getPrNumber(), pr.getState(),
                            shortHash(pr.getCommitHash()), targetBranchName);
                });
            }
        }

        if (prAnalyzedCommits.isEmpty()) {
            log.debug("PRs found but none have completed analyses — all {} commits uncovered",
                    unanalyzedCommits.size());
            return new CoverageResult(CoverageStatus.NOT_COVERED, new ArrayList<>(unanalyzedCommits));
        }

        // Determine which unanalyzed commits are covered
        List<String> uncovered = unanalyzedCommits.stream()
                .filter(hash -> !prAnalyzedCommits.contains(hash))
                .collect(Collectors.toList());

        if (uncovered.isEmpty()) {
            log.info("All {} unanalyzed commits are covered by PR analyses on branch {}",
                    unanalyzedCommits.size(), targetBranchName);
            return new CoverageResult(CoverageStatus.FULLY_COVERED, Collections.emptyList());
        } else if (uncovered.size() < unanalyzedCommits.size()) {
            log.info("{}/{} unanalyzed commits covered by PR analyses on branch {} — {} uncovered",
                    unanalyzedCommits.size() - uncovered.size(), unanalyzedCommits.size(),
                    targetBranchName, uncovered.size());
            return new CoverageResult(CoverageStatus.PARTIALLY_COVERED, uncovered);
        } else {
            log.info("No unanalyzed commits covered by PR analyses on branch {}",
                    targetBranchName);
            return new CoverageResult(CoverageStatus.NOT_COVERED, new ArrayList<>(unanalyzedCommits));
        }
    }

    private static String shortHash(String hash) {
        return hash != null ? hash.substring(0, Math.min(7, hash.length())) : "null";
    }
}

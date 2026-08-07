package org.rostilos.codecrow.commitgraph.service;

import org.rostilos.codecrow.commitgraph.dag.CommitRangeContext;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.model.VcsCommit;
import org.rostilos.codecrow.scmevidence.service.ScmEvidenceService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.beans.factory.annotation.Autowired;

import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * Determines which commits need analysis on a branch push.
 * <p>
 * Replaces the old {@code DagSyncService} which built a full git graph in the DB.
 * This service uses a simple, reliable approach:
 * <ol>
 *   <li>Fetch recent commit history from VCS API</li>
 *   <li>Subtract already-analyzed commits (from {@code analyzed_commit} table)</li>
 *   <li>Return the remaining commits as the work to be done</li>
 * </ol>
 * <p>
 * The diff base for the range diff is simply {@code Branch.lastKnownHeadCommit}
 * — no BFS/DFS graph walking required.
 */
@Service
public class BranchCommitService {

    private static final Logger log = LoggerFactory.getLogger(BranchCommitService.class);
    // Preserve evidence for long-lived branches instead of collapsing a 400+
    // commit promotion to a HEAD-only fallback.
    private static final int DEFAULT_COMMIT_FETCH_LIMIT = 1000;

    private final VcsClientProvider vcsClientProvider;
    private final AnalyzedCommitService analyzedCommitService;
    private final BranchRepository branchRepository;
    private final ScmEvidenceService scmEvidenceService;

    public BranchCommitService(
            VcsClientProvider vcsClientProvider,
            AnalyzedCommitService analyzedCommitService,
            BranchRepository branchRepository
    ) {
        this(vcsClientProvider, analyzedCommitService, branchRepository, null);
    }

    @Autowired
    public BranchCommitService(
            VcsClientProvider vcsClientProvider,
            AnalyzedCommitService analyzedCommitService,
            BranchRepository branchRepository,
            ScmEvidenceService scmEvidenceService
    ) {
        this.vcsClientProvider = vcsClientProvider;
        this.analyzedCommitService = analyzedCommitService;
        this.branchRepository = branchRepository;
        this.scmEvidenceService = scmEvidenceService;
    }

    /**
     * Resolve the commit range that needs analysis for a branch push.
     *
     * @param project          the project
     * @param vcsConnection    the VCS connection to use
     * @param targetBranchName the branch being pushed to
     * @param commitHash       the new HEAD commit hash
     * @return context with unanalyzed commits, diff base, and skip flag
     */
    public CommitRangeContext resolveCommitRange(
            Project project,
            VcsConnection vcsConnection,
            String targetBranchName,
            String commitHash) {

        // Look up the branch to get lastKnownHeadCommit (our diff base)
        Optional<Branch> branchOpt = branchRepository.findByProjectIdAndBranchName(
                project.getId(), targetBranchName);
        String lastKnownHead = branchOpt
                .map(Branch::getLastKnownHeadCommit)
                .orElse(null);

        // ── First analysis: no previous HEAD known ───────────────────────
        if (lastKnownHead == null) {
            log.info("First analysis for branch {} (no lastKnownHeadCommit) — scope limited to HEAD commit only",
                    targetBranchName);
            return CommitRangeContext.firstAnalysis(commitHash);
        }

        // ── Same commit: nothing to do ───────────────────────────────────
        if (lastKnownHead.equals(commitHash)) {
            // Check if this commit is already analyzed
            boolean multiBranch = project.getConfiguration() != null
                    && project.getConfiguration().ragConfig() != null
                    && project.getConfiguration().ragConfig().isMultiBranchEnabled();
            boolean analyzed = multiBranch
                    ? analyzedCommitService.isAnalyzed(
                            project.getId(), targetBranchName, commitHash)
                    : analyzedCommitService.isAnalyzed(
                            project.getId(), commitHash);
            if (analyzed) {
                log.info("HEAD commit {} is already analyzed — skipping", shortHash(commitHash));
                return CommitRangeContext.skip();
            }
            // Same HEAD but not analyzed (previous analysis failed?) — analyze it
            return new CommitRangeContext(List.of(commitHash), null, false);
        }

        // ── Normal case: fetch commits between lastKnownHead and new HEAD ──
        try {
            VcsClient vcsClient = vcsClientProvider.getClient(vcsConnection);
            String workspace = project.getEffectiveVcsRepoInfo().getRepoWorkspace();
            String slug = project.getEffectiveVcsRepoInfo().getRepoSlug();

            // Fetch recent commit history for the branch
            List<VcsCommit> commits = vcsClient.getCommitHistory(
                    workspace, slug, targetBranchName, DEFAULT_COMMIT_FETCH_LIMIT);

            if (commits == null || commits.isEmpty()) {
                log.warn("No commits returned from VCS for branch {} — falling back to HEAD only",
                        targetBranchName);
                return new CommitRangeContext(List.of(commitHash), lastKnownHead, false);
            }

            // Extract hashes from the commit history, stopping at lastKnownHead
            // (commits are returned newest-first from VCS API)
            List<String> commitsSinceLastHead = new java.util.ArrayList<>();
            boolean lastKnownHeadFound = false;
            for (VcsCommit c : commits) {
                if (c.hash().equals(lastKnownHead)) {
                    lastKnownHeadFound = true;
                    break; // Reached the boundary of what we already know
                }
                commitsSinceLastHead.add(c.hash());
            }

            if (!lastKnownHeadFound) {
                // lastKnownHead wasn't found in the window — might be a force push
                // or the commit is older than the fetch limit
                log.info("lastKnownHead {} not found in recent {} commits for branch {} — " +
                                "possible force push or large gap. Using HEAD only.",
                        shortHash(lastKnownHead), commits.size(), targetBranchName);
                return new CommitRangeContext(List.of(commitHash), lastKnownHead, false);
            }

            // Reverse to chronological order (oldest first)
            Collections.reverse(commitsSinceLastHead);

            if (scmEvidenceService != null) {
                try {
                    Map<String, VcsCommit> commitsByHash = commits.stream()
                            .collect(java.util.stream.Collectors.toMap(
                                    VcsCommit::hash,
                                    commit -> commit,
                                    (first, ignored) -> first));
                    List<VcsCommit> evidenceCommits = commitsSinceLastHead.stream()
                            .map(commitsByHash::get)
                            .filter(java.util.Objects::nonNull)
                            .toList();
                    scmEvidenceService.capture(
                            project.getId(), vcsClient, workspace, slug,
                            evidenceCommits);
                    var promotion = scmEvidenceService.planPromotion(
                            project.getId(), commitsSinceLastHead,
                            targetBranchName, lastKnownHead);
                    log.info("SCM promotion evidence for branch {}: reuse={}, targetContextAnalysis={}",
                            targetBranchName, promotion.reuseKind(),
                            promotion.requiresTargetContextAnalysis());
                } catch (Exception evidenceFailure) {
                    log.warn("SCM evidence enrichment unavailable for branch {}: {}",
                            targetBranchName, evidenceFailure.getMessage());
                }
            }

            // Subtract already-analyzed commits
            boolean multiBranch = project.getConfiguration() != null
                    && project.getConfiguration().ragConfig() != null
                    && project.getConfiguration().ragConfig().isMultiBranchEnabled();
            List<String> unanalyzed = multiBranch
                    ? analyzedCommitService.filterUnanalyzed(
                            project.getId(), targetBranchName, commitsSinceLastHead)
                    : analyzedCommitService.filterUnanalyzed(
                            project.getId(), commitsSinceLastHead);

            if (unanalyzed.isEmpty()) {
                log.info("All {} commits since lastKnownHead are already analyzed — skipping",
                        commitsSinceLastHead.size());
                return CommitRangeContext.skip();
            }

            log.info("Resolved {} unanalyzed commits out of {} new commits (branch={}, base={}..{})",
                    unanalyzed.size(), commitsSinceLastHead.size(), targetBranchName,
                    shortHash(lastKnownHead), shortHash(commitHash));

            return new CommitRangeContext(unanalyzed, lastKnownHead, false);

        } catch (Exception e) {
            log.warn("Failed to resolve commit range from VCS for branch {} — falling back to HEAD only: {}",
                    targetBranchName, e.getMessage());
            return new CommitRangeContext(List.of(commitHash), lastKnownHead, false);
        }
    }

    private static String shortHash(String hash) {
        return hash != null ? hash.substring(0, Math.min(7, hash.length())) : "null";
    }
}

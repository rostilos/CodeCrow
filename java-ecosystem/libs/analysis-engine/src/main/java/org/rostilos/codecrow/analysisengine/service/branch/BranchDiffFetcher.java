package org.rostilos.codecrow.analysisengine.service.branch;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.commitgraph.dag.CommitRangeContext;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.processor.VcsRepoInfoImpl;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.util.List;

@Service
public class BranchDiffFetcher {
    private static final Logger log = LoggerFactory.getLogger(BranchDiffFetcher.class);

    public String fetchDiff(BranchProcessRequest request, Branch existingBranch,
                            CommitRangeContext rangeCtx, VcsOperationsService operationsService,
                            OkHttpClient client, VcsRepoInfoImpl vcsRepoInfoImpl,
                            Long prNumber, List<String> unanalyzedCommits) throws IOException {

        String lastSuccessfulCommit = existingBranch != null
                ? existingBranch.getLastSuccessfulCommitHash() : null;
        boolean isFirstAnalysis = lastSuccessfulCommit == null;
        String rawDiff = null;

        // ── First-analysis fast path ──────────────────────────────────────
        // On a brand-new project/branch (no prior successful analysis), DAG
        // or aggregated diffs can cover the ENTIRE commit history — e.g. when
        // a merged PR's commits create an analyzed ancestor on a different
        // parent line, the range diff ancestor..HEAD spans every file in the
        // repo.  Instead: use the PR diff if a merge triggered the analysis
        // (scoped to just the PR's changes), or the single HEAD commit diff.
        if (isFirstAnalysis) {
            if (prNumber != null) {
                try {
                    rawDiff = operationsService.getPullRequestDiff(
                            client, vcsRepoInfoImpl.workspace(), vcsRepoInfoImpl.repoSlug(),
                            String.valueOf(prNumber));
                    if (rawDiff != null && !rawDiff.isBlank()) {
                        log.info("First analysis: using PR #{} diff for branch {} (scoped to PR changes only)",
                                prNumber, request.getTargetBranchName());
                        return rawDiff;
                    }
                } catch (Exception e) {
                    log.warn("First analysis: PR #{} diff fetch failed, falling back to commit diff: {}",
                            prNumber, e.getMessage());
                }
            }
            rawDiff = operationsService.getCommitDiff(
                    client, vcsRepoInfoImpl.workspace(), vcsRepoInfoImpl.repoSlug(),
                    request.getCommitHash());
            log.info("First analysis for branch {} — using single commit diff to establish baseline",
                    request.getTargetBranchName());
            return rawDiff;
        }

        // ── Subsequent analysis: multi-tier diff strategy ─────────────────

        // Tier 0: PR diff — most precise scope when a PR merge triggered this.
        // The PR diff is exactly the set of changes the PR introduced, with no
        // risk of picking up unrelated commits from range diffs.
        if (prNumber != null) {
            try {
                rawDiff = operationsService.getPullRequestDiff(
                        client, vcsRepoInfoImpl.workspace(), vcsRepoInfoImpl.repoSlug(),
                        String.valueOf(prNumber));
                if (rawDiff != null && !rawDiff.isBlank()) {
                    log.info("Using PR #{} diff for branch analysis on {} (precisely scoped to merged changes)",
                            prNumber, request.getTargetBranchName());
                    return rawDiff;
                }
            } catch (Exception e) {
                log.warn("PR #{} diff fetch failed, falling back to range diff: {}", prNumber, e.getMessage());
            }
        }

        // Tier 1: Range diff from lastKnownHeadCommit (for direct pushes / no PR context)
        rawDiff = tryDagDiff(rangeCtx, request, operationsService, client, vcsRepoInfoImpl, unanalyzedCommits);

        // Tier 2: Range diff from lastSuccessfulCommit
        if (rawDiff == null) {
            rawDiff = tryDeltaDiff(lastSuccessfulCommit, request, operationsService, client, vcsRepoInfoImpl);
        }

        // Tier 2.5: Aggregate individual commit diffs when range diff failed.
        if (rawDiff == null && !unanalyzedCommits.isEmpty()) {
            rawDiff = tryAggregatedCommitDiffs(unanalyzedCommits, operationsService, client, vcsRepoInfoImpl);
        }

        // Tier 3: Single commit diff (last resort)
        if (rawDiff == null) {
            rawDiff = operationsService.getCommitDiff(
                    client, vcsRepoInfoImpl.workspace(), vcsRepoInfoImpl.repoSlug(), request.getCommitHash());
            log.info("Fetched commit {} diff for branch analysis (last resort)",
                    request.getCommitHash());
        }

        return rawDiff;
    }

    private String tryDagDiff(CommitRangeContext rangeCtx, BranchProcessRequest request,
                              VcsOperationsService operationsService, OkHttpClient client,
                              VcsRepoInfoImpl vcsRepoInfoImpl, List<String> unanalyzedCommits) {
        if (rangeCtx.getDiffBase() == null || request.getCommitHash() == null
                || rangeCtx.getDiffBase().equals(request.getCommitHash())) {
            return null;
        }
        try {
            String diff = operationsService.getCommitRangeDiff(
                    client, vcsRepoInfoImpl.workspace(), vcsRepoInfoImpl.repoSlug(),
                    rangeCtx.getDiffBase(), request.getCommitHash());
            if (diff != null && diff.isBlank()) {
                log.info("Range diff ({}..{}) returned empty (likely merge commit) — falling through",
                        shortHash(rangeCtx.getDiffBase()), shortHash(request.getCommitHash()));
                return null;
            }
            log.info("Fetched range diff ({}..{}) — covers {} unanalyzed commits",
                    shortHash(rangeCtx.getDiffBase()), shortHash(request.getCommitHash()),
                    unanalyzedCommits.size());
            return diff;
        } catch (IOException e) {
            log.warn("Range diff failed (base {} may be unreachable), falling back: {}",
                    shortHash(rangeCtx.getDiffBase()), e.getMessage());
            return null;
        }
    }

    private String tryDeltaDiff(String lastSuccessfulCommit, BranchProcessRequest request,
                                VcsOperationsService operationsService, OkHttpClient client,
                                VcsRepoInfoImpl vcsRepoInfoImpl) {
        if (lastSuccessfulCommit == null || lastSuccessfulCommit.equals(request.getCommitHash())) {
            return null;
        }
        try {
            String diff = operationsService.getCommitRangeDiff(
                    client, vcsRepoInfoImpl.workspace(), vcsRepoInfoImpl.repoSlug(),
                    lastSuccessfulCommit, request.getCommitHash());
            if (diff != null && diff.isBlank()) {
                log.info("Delta diff ({}..{}) returned empty — falling through to next tier",
                        shortHash(lastSuccessfulCommit), shortHash(request.getCommitHash()));
                return null;
            }
            log.info("Fetched delta diff ({}..{}) for branch analysis — captures all changes since last success",
                    shortHash(lastSuccessfulCommit), shortHash(request.getCommitHash()));
            return diff;
        } catch (IOException e) {
            log.warn("Delta diff failed (base commit {} may no longer exist), falling back: {}",
                    shortHash(lastSuccessfulCommit), e.getMessage());
            return null;
        }
    }

    private String tryAggregatedCommitDiffs(List<String> unanalyzedCommits,
                                            VcsOperationsService operationsService,
                                            OkHttpClient client, VcsRepoInfoImpl vcsRepoInfoImpl) {
        int maxCommits = Math.min(unanalyzedCommits.size(), 50);
        log.info("Range diff unavailable — aggregating individual diffs for {} of {} unanalyzed commits",
                maxCommits, unanalyzedCommits.size());

        StringBuilder aggregatedDiff = new StringBuilder();
        int fetchedCount = 0;

        for (int i = 0; i < maxCommits; i++) {
            String hash = unanalyzedCommits.get(i);
            try {
                String commitDiff = operationsService.getCommitDiff(
                        client, vcsRepoInfoImpl.workspace(), vcsRepoInfoImpl.repoSlug(), hash);
                if (commitDiff != null && !commitDiff.isBlank()) {
                    aggregatedDiff.append(commitDiff);
                    if (!commitDiff.endsWith("\n")) {
                        aggregatedDiff.append("\n");
                    }
                    fetchedCount++;
                }
            } catch (Exception e) {
                log.warn("Failed to fetch diff for commit {} (skipping): {}",
                        shortHash(hash), e.getMessage());
            }
        }

        if (fetchedCount > 0) {
            String result = aggregatedDiff.toString();
            log.info("Aggregated {} individual commit diffs ({} chars) as fallback for empty range diff",
                    fetchedCount, result.length());
            return result;
        }
        return null;
    }

    private static String shortHash(String hash) {
        return hash != null ? hash.substring(0, Math.min(7, hash.length())) : "null";
    }
}

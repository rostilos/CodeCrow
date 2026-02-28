package org.rostilos.codecrow.analysisengine.service.branch;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.analysisengine.dag.DagContext;
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
                            DagContext dagCtx, VcsOperationsService operationsService,
                            OkHttpClient client, VcsRepoInfoImpl vcsRepoInfoImpl,
                            Long prNumber, List<String> unanalyzedCommits) throws IOException {

        String lastSuccessfulCommit = existingBranch != null
                ? existingBranch.getLastSuccessfulCommitHash() : null;
        String rawDiff = null;

        // Tier 0: DAG-derived diff base (preferred)
        rawDiff = tryDagDiff(dagCtx, request, operationsService, client, vcsRepoInfoImpl, unanalyzedCommits);

        // Tier 1: Legacy delta diff
        if (rawDiff == null) {
            rawDiff = tryDeltaDiff(lastSuccessfulCommit, request, operationsService, client, vcsRepoInfoImpl);
        }

        // Tier 1.5: Aggregate individual commit diffs when range diff failed.
        // Handles cases where the base commit is a second-parent commit (e.g. from
        // a merged feature branch) and the range diff API returns empty.
        if (rawDiff == null && !unanalyzedCommits.isEmpty()) {
            rawDiff = tryAggregatedCommitDiffs(unanalyzedCommits, operationsService, client, vcsRepoInfoImpl);
        }

        // Tier 2: PR diff
        if (rawDiff == null && prNumber != null) {
            rawDiff = operationsService.getPullRequestDiff(
                    client, vcsRepoInfoImpl.workspace(), vcsRepoInfoImpl.repoSlug(), String.valueOf(prNumber));
            log.info("Fetched PR #{} diff for branch analysis (first analysis or delta fallback)", prNumber);
        }

        // Tier 3: Single commit diff (last resort)
        if (rawDiff == null) {
            rawDiff = operationsService.getCommitDiff(
                    client, vcsRepoInfoImpl.workspace(), vcsRepoInfoImpl.repoSlug(), request.getCommitHash());
            log.info("Fetched commit {} diff for branch analysis (first analysis, no delta or PR context)",
                    request.getCommitHash());
        }

        return rawDiff;
    }

    private String tryDagDiff(DagContext dagCtx, BranchProcessRequest request,
                              VcsOperationsService operationsService, OkHttpClient client,
                              VcsRepoInfoImpl vcsRepoInfoImpl, List<String> unanalyzedCommits) {
        if (dagCtx.getDiffBase() == null || request.getCommitHash() == null
                || dagCtx.getDiffBase().equals(request.getCommitHash())) {
            return null;
        }
        try {
            String diff = operationsService.getCommitRangeDiff(
                    client, vcsRepoInfoImpl.workspace(), vcsRepoInfoImpl.repoSlug(),
                    dagCtx.getDiffBase(), request.getCommitHash());
            if (diff != null && diff.isBlank()) {
                log.info("DAG-based diff ({}..{}) returned empty (likely merge commit) — falling through",
                        shortHash(dagCtx.getDiffBase()), shortHash(request.getCommitHash()));
                return null;
            }
            log.info("Fetched DAG-based diff ({}..{}) — covers {} unanalyzed commits",
                    shortHash(dagCtx.getDiffBase()), shortHash(request.getCommitHash()),
                    unanalyzedCommits.size());
            return diff;
        } catch (IOException e) {
            log.warn("DAG-based diff failed (ancestor {} may be unreachable), falling back: {}",
                    shortHash(dagCtx.getDiffBase()), e.getMessage());
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

package org.rostilos.codecrow.analysisengine.service.pr;

import java.io.IOException;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.List;

import org.rostilos.codecrow.analysisengine.util.AnalysisLimitEnforcer;
import org.rostilos.codecrow.analysisengine.util.AnalysisScopeFilter;
import org.rostilos.codecrow.analysisengine.util.DiffParser;
import org.rostilos.codecrow.analysisengine.util.DiffParsingUtils;
import org.rostilos.codecrow.analysisengine.util.TokenEstimator;
import org.rostilos.codecrow.analysisengine.util.VcsDiffUtils;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.project.Project;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

/**
 * Provider-neutral preparation of pull-request diffs before enrichment or AI.
 * VCS adapters supply raw full and commit-range diffs; this service owns every
 * analysis policy applied to those diffs.
 */
@Service
public class PullRequestDiffPreparationService {
    private static final Logger log = LoggerFactory.getLogger(PullRequestDiffPreparationService.class);

    private final AnalysisLimitEnforcer limitEnforcer;

    @Autowired
    public PullRequestDiffPreparationService(AnalysisLimitEnforcer limitEnforcer) {
        this.limitEnforcer = limitEnforcer;
    }

    public PreparedDiff prepare(
            Project project,
            Long pullRequestId,
            String rawFullDiff,
            String previousCommitHash,
            String currentCommitHash,
            CommitRangeDiffFetcher deltaDiffFetcher) {
        // The proposed PR tree is target + the complete base-to-head patch. It
        // must not inherit review-scope filtering or an incremental Stage 1
        // selection, otherwise unchanged batches can observe stale target-head
        // files while the overlay is incorrectly described as exact.
        ProposedTreePaths proposedTreePaths = proposedTreePaths(rawFullDiff);

        String scopedFullDiff = AnalysisScopeFilter.filterDiff(rawFullDiff, project);
        if (scopedFullDiff == null || scopedFullDiff.isBlank()) {
            return PreparedDiff.empty(previousCommitHash, currentCommitHash);
        }

        // Preserve the complete scoped evidence. Prompt budgeting and hunk
        // batching happen downstream; replacing a large file with a placeholder
        // here permanently loses reviewable changes and produces false negatives.
        String fullDiff = scopedFullDiff;

        AnalysisMode mode = AnalysisMode.FULL;
        String scopedDeltaDiff = null;
        String deltaDiff = null;
        if (canUseIncremental(previousCommitHash, currentCommitHash)) {
            scopedDeltaDiff = fetchDeltaDiff(deltaDiffFetcher, previousCommitHash, currentCommitHash);
            scopedDeltaDiff = AnalysisScopeFilter.filterDiff(scopedDeltaDiff, project);
            deltaDiff = scopedDeltaDiff;
            if (isUsefulDelta(deltaDiff, fullDiff)) {
                mode = AnalysisMode.INCREMENTAL;
            } else {
                scopedDeltaDiff = null;
                deltaDiff = null;
            }
        }

        String unfilteredSelectedDiff = mode == AnalysisMode.INCREMENTAL ? scopedDeltaDiff : scopedFullDiff;
        String selectedDiff = mode == AnalysisMode.INCREMENTAL ? deltaDiff : fullDiff;
        limitEnforcer.enforce(project, pullRequestId, unfilteredSelectedDiff);
        logTokenEstimate(project, pullRequestId, selectedDiff);

        List<String> changedFiles = DiffParser.extractChangedFiles(selectedDiff);
        List<String> deletedFiles = DiffParser.extractDeletedFiles(selectedDiff);
        log.info("Prepared {} analysis diff with {} changed and {} deleted files",
                mode, changedFiles.size(), deletedFiles.size());

        return new PreparedDiff(
                fullDiff, deltaDiff, mode, changedFiles, deletedFiles,
                proposedTreePaths.changedFiles(), proposedTreePaths.deletedFiles(),
                previousCommitHash, currentCommitHash);
    }

    private ProposedTreePaths proposedTreePaths(String rawFullDiff) {
        LinkedHashSet<String> changed = new LinkedHashSet<>();
        LinkedHashSet<String> deleted = new LinkedHashSet<>();
        for (DiffParsingUtils.FileChange change : DiffParsingUtils.parseFileChanges(rawFullDiff)) {
            if (change.newPath() != null && !change.newPath().isBlank()) {
                changed.add(change.newPath());
            }
            if ((change.changeType() == DiffParsingUtils.ChangeType.DELETED
                    || change.changeType() == DiffParsingUtils.ChangeType.RENAMED)
                    && change.oldPath() != null
                    && !change.oldPath().isBlank()) {
                deleted.add(change.oldPath());
            }
        }
        return new ProposedTreePaths(List.copyOf(changed), List.copyOf(deleted));
    }

    private boolean canUseIncremental(String previousCommitHash, String currentCommitHash) {
        return previousCommitHash != null
                && currentCommitHash != null
                && !previousCommitHash.equals(currentCommitHash);
    }

    private String fetchDeltaDiff(
            CommitRangeDiffFetcher fetcher,
            String previousCommitHash,
            String currentCommitHash) {
        try {
            return fetcher.fetch(previousCommitHash, currentCommitHash);
        } catch (IOException e) {
            log.warn("Unable to fetch incremental diff from {} to {}: {}",
                    abbreviate(previousCommitHash), abbreviate(currentCommitHash), e.getMessage());
            return null;
        }
    }

    private boolean isUsefulDelta(String deltaDiff, String fullDiff) {
        if (deltaDiff == null || deltaDiff.isBlank()) {
            log.info("No incremental diff available; using full analysis");
            return false;
        }
        if (deltaDiff.length() < VcsDiffUtils.MIN_DELTA_DIFF_SIZE) {
            log.info("Incremental diff is too small ({} chars); using full analysis", deltaDiff.length());
            return false;
        }
        if (VcsDiffUtils.shouldEscalateToFull(deltaDiff.length(), fullDiff != null ? fullDiff.length() : 0)) {
            log.info("Incremental diff is too large relative to the full diff; using full analysis");
            return false;
        }
        return true;
    }

    private void logTokenEstimate(Project project, Long pullRequestId, String diff) {
        int maxTokens = project.getEffectiveConfig().maxAnalysisTokenLimit();
        TokenEstimator.TokenEstimationResult estimate = TokenEstimator.estimateAndCheck(diff, maxTokens);
        log.info("PR diff token estimate: {}", estimate.toLogString());
        if (estimate.exceedsLimit()) {
            log.info("PR diff will use map-reduce chunking: project={}, PR={}, tokens={}/{}",
                    project.getId(), pullRequestId, estimate.estimatedTokens(), estimate.maxAllowedTokens());
        }
    }

    private String abbreviate(String hash) {
        return hash != null && hash.length() > 7 ? hash.substring(0, 7) : String.valueOf(hash);
    }

    @FunctionalInterface
    public interface CommitRangeDiffFetcher {
        String fetch(String baseCommit, String headCommit) throws IOException;
    }

    private record ProposedTreePaths(List<String> changedFiles, List<String> deletedFiles) {
    }

    public record PreparedDiff(
            String fullDiff,
            String deltaDiff,
            AnalysisMode analysisMode,
            List<String> changedFiles,
            List<String> deletedFiles,
            List<String> proposedTreeChangedFiles,
            List<String> proposedTreeDeletedFiles,
            String previousCommitHash,
            String currentCommitHash) {

        public PreparedDiff {
            changedFiles = changedFiles != null ? List.copyOf(changedFiles) : Collections.emptyList();
            deletedFiles = deletedFiles != null ? List.copyOf(deletedFiles) : Collections.emptyList();
            proposedTreeChangedFiles = proposedTreeChangedFiles != null
                    ? List.copyOf(proposedTreeChangedFiles)
                    : Collections.emptyList();
            proposedTreeDeletedFiles = proposedTreeDeletedFiles != null
                    ? List.copyOf(proposedTreeDeletedFiles)
                    : Collections.emptyList();
        }

        public static PreparedDiff empty(String previousCommitHash, String currentCommitHash) {
            return new PreparedDiff(null, null, AnalysisMode.FULL,
                    List.of(), List.of(), List.of(), List.of(),
                    previousCommitHash, currentCommitHash);
        }

        public boolean isEmpty() {
            return selectedDiff() == null || selectedDiff().isBlank();
        }

        public String selectedDiff() {
            return analysisMode == AnalysisMode.INCREMENTAL ? deltaDiff : fullDiff;
        }
    }
}

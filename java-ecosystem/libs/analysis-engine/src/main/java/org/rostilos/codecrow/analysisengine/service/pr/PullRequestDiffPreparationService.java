package org.rostilos.codecrow.analysisengine.service.pr;

import java.io.IOException;
import java.util.Collections;
import java.util.List;

import org.rostilos.codecrow.analysisengine.util.AnalysisLimitEnforcer;
import org.rostilos.codecrow.analysisengine.util.AnalysisScopeFilter;
import org.rostilos.codecrow.analysisengine.util.DiffContentFilter;
import org.rostilos.codecrow.analysisengine.util.DiffParser;
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

    private final DiffContentFilter contentFilter;
    private final AnalysisLimitEnforcer limitEnforcer;

    @Autowired
    public PullRequestDiffPreparationService(AnalysisLimitEnforcer limitEnforcer) {
        this(new DiffContentFilter(), limitEnforcer);
    }

    PullRequestDiffPreparationService(
            DiffContentFilter contentFilter,
            AnalysisLimitEnforcer limitEnforcer) {
        this.contentFilter = contentFilter;
        this.limitEnforcer = limitEnforcer;
    }

    public PreparedDiff prepare(
            Project project,
            Long pullRequestId,
            String rawFullDiff,
            String previousCommitHash,
            String currentCommitHash,
            CommitRangeDiffFetcher deltaDiffFetcher) {
        String scopedFullDiff = AnalysisScopeFilter.filterDiff(rawFullDiff, project);
        if (scopedFullDiff == null || scopedFullDiff.isBlank()) {
            return PreparedDiff.empty(previousCommitHash, currentCommitHash);
        }

        String fullDiff = contentFilter.filterDiff(scopedFullDiff);
        logFiltering(rawFullDiff, fullDiff);

        AnalysisMode mode = AnalysisMode.FULL;
        String scopedDeltaDiff = null;
        String deltaDiff = null;
        if (canUseIncremental(previousCommitHash, currentCommitHash)) {
            scopedDeltaDiff = fetchDeltaDiff(deltaDiffFetcher, previousCommitHash, currentCommitHash);
            scopedDeltaDiff = AnalysisScopeFilter.filterDiff(scopedDeltaDiff, project);
            deltaDiff = contentFilter.filterDiff(scopedDeltaDiff);
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
                previousCommitHash, currentCommitHash);
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

    private void logFiltering(String rawDiff, String filteredDiff) {
        int originalSize = rawDiff != null ? rawDiff.length() : 0;
        int filteredSize = filteredDiff != null ? filteredDiff.length() : 0;
        if (originalSize != filteredSize) {
            log.info("PR diff filtered from {} to {} chars ({}% reduction)",
                    originalSize, filteredSize,
                    originalSize > 0 ? 100 - (filteredSize * 100 / originalSize) : 0);
        }
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

    public record PreparedDiff(
            String fullDiff,
            String deltaDiff,
            AnalysisMode analysisMode,
            List<String> changedFiles,
            List<String> deletedFiles,
            String previousCommitHash,
            String currentCommitHash) {

        public PreparedDiff {
            changedFiles = changedFiles != null ? List.copyOf(changedFiles) : Collections.emptyList();
            deletedFiles = deletedFiles != null ? List.copyOf(deletedFiles) : Collections.emptyList();
        }

        public static PreparedDiff empty(String previousCommitHash, String currentCommitHash) {
            return new PreparedDiff(null, null, AnalysisMode.FULL, List.of(), List.of(),
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

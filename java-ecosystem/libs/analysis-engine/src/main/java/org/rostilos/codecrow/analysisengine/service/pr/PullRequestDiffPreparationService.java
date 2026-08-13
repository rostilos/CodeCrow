package org.rostilos.codecrow.analysisengine.service.pr;

import java.io.IOException;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;

import org.rostilos.codecrow.analysisengine.util.AnalysisLimitEnforcer;
import org.rostilos.codecrow.analysisengine.util.AnalysisScopeFilter;
import org.rostilos.codecrow.analysisengine.util.DiffParser;
import org.rostilos.codecrow.analysisengine.util.TokenEstimator;
import org.rostilos.codecrow.analysisengine.util.VcsDiffUtils;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.vcsclient.model.VcsPullRequestChangeManifest;
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
        return prepare(
                project,
                pullRequestId,
                rawFullDiff,
                previousCommitHash,
                currentCommitHash,
                VcsPullRequestChangeManifest.unavailable(
                        "provider manifest was not supplied"),
                deltaDiffFetcher);
    }

    public PreparedDiff prepare(
            Project project,
            Long pullRequestId,
            String rawFullDiff,
            String previousCommitHash,
            String currentCommitHash,
            VcsPullRequestChangeManifest providerManifest,
            CommitRangeDiffFetcher deltaDiffFetcher) {
        VcsPullRequestChangeManifest fullManifest = AnalysisScopeFilter.filterManifest(
                mergePatchFallback(providerManifest, rawFullDiff), project);
        String scopedFullDiff = AnalysisScopeFilter.filterDiff(rawFullDiff, project);
        if (scopedFullDiff == null || scopedFullDiff.isBlank()) {
            return PreparedDiff.withoutReviewableDiff(
                    fullManifest, previousCommitHash, currentCommitHash);
        }

        // Preserve the complete scoped evidence. Prompt budgeting and hunk
        // batching happen downstream; replacing a large file with a placeholder
        // here permanently loses reviewable changes and produces false negatives.
        String fullDiff = scopedFullDiff;

        AnalysisMode mode = AnalysisMode.FULL;
        String scopedDeltaDiff = null;
        String deltaDiff = null;
        if (canUseIncremental(previousCommitHash, currentCommitHash)) {
            String rawDeltaDiff = fetchDeltaDiff(
                    deltaDiffFetcher, previousCommitHash, currentCommitHash);
            scopedDeltaDiff = AnalysisScopeFilter.filterDiff(rawDeltaDiff, project);
            deltaDiff = scopedDeltaDiff;
            if (rawDeltaDiff != null && (scopedDeltaDiff == null || scopedDeltaDiff.isBlank())) {
                // A successful compare may contain only excluded/non-reviewable
                // paths. Preserve the incremental boundary so callers can
                // maintain the full snapshot without re-reviewing old hunks.
                mode = AnalysisMode.INCREMENTAL;
            } else if (isUsefulDelta(deltaDiff, fullDiff)) {
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
                fullDiff, deltaDiff, mode, changedFiles, deletedFiles, fullManifest,
                previousCommitHash, currentCommitHash);
    }

    private VcsPullRequestChangeManifest mergePatchFallback(
            VcsPullRequestChangeManifest providerManifest,
            String rawFullDiff) {
        VcsPullRequestChangeManifest supplied = providerManifest != null
                ? providerManifest
                : VcsPullRequestChangeManifest.unavailable("provider manifest was null");
        if (supplied.isComplete()) {
            return supplied;
        }

        LinkedHashMap<String, VcsPullRequestChangeManifest.Change> changes =
                new LinkedHashMap<>();
        for (VcsPullRequestChangeManifest.Change change : supplied.changes()) {
            if (change != null && !change.path().isBlank()) {
                changes.putIfAbsent(change.path(), change);
            }
        }
        for (DiffParser.DiffFileInfo file : DiffParser.parseDiff(rawFullDiff, 0)) {
            if (file.getPath() == null || file.getPath().isBlank()) {
                continue;
            }
            changes.putIfAbsent(
                    file.getPath(),
                    new VcsPullRequestChangeManifest.Change(
                            file.getPath(),
                            "",
                            fallbackChangeKind(file.getChangeType())));
        }

        String receipt = supplied.receipt();
        if (!changes.isEmpty()) {
            receipt = (receipt.isBlank() ? "" : receipt + ";")
                    + "unified-diff-fallback:incomplete";
        }
        return new VcsPullRequestChangeManifest(
                List.copyOf(changes.values()), supplied.completeness(), receipt);
    }

    private VcsPullRequestChangeManifest.ChangeKind fallbackChangeKind(String changeType) {
        return switch (changeType != null ? changeType.toLowerCase() : "") {
            case "added" -> VcsPullRequestChangeManifest.ChangeKind.ADDED;
            case "deleted" -> VcsPullRequestChangeManifest.ChangeKind.DELETED;
            case "renamed" -> VcsPullRequestChangeManifest.ChangeKind.RENAMED;
            case "modified" -> VcsPullRequestChangeManifest.ChangeKind.MODIFIED;
            default -> VcsPullRequestChangeManifest.ChangeKind.UNKNOWN;
        };
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

    public record PreparedDiff(
            String fullDiff,
            String deltaDiff,
            AnalysisMode analysisMode,
            List<String> changedFiles,
            List<String> deletedFiles,
            VcsPullRequestChangeManifest fullManifest,
            String previousCommitHash,
            String currentCommitHash) {

        public PreparedDiff {
            changedFiles = changedFiles != null ? List.copyOf(changedFiles) : Collections.emptyList();
            deletedFiles = deletedFiles != null ? List.copyOf(deletedFiles) : Collections.emptyList();
            fullManifest = fullManifest != null
                    ? fullManifest
                    : VcsPullRequestChangeManifest.unavailable("manifest was not prepared");
        }

        public static PreparedDiff empty(String previousCommitHash, String currentCommitHash) {
            return withoutReviewableDiff(
                    VcsPullRequestChangeManifest.unavailable("diff was not prepared"),
                    previousCommitHash,
                    currentCommitHash);
        }

        public static PreparedDiff withoutReviewableDiff(
                VcsPullRequestChangeManifest fullManifest,
                String previousCommitHash,
                String currentCommitHash) {
            return new PreparedDiff(null, null, AnalysisMode.FULL, List.of(), List.of(), fullManifest,
                    previousCommitHash, currentCommitHash);
        }

        public boolean isEmpty() {
            return !hasReviewableDiff()
                    && fullManifest.changes().isEmpty()
                    && !fullManifest.isComplete();
        }

        public boolean hasReviewableDiff() {
            return selectedDiff() != null && !selectedDiff().isBlank();
        }

        /**
         * Non-blank request evidence for context-only work. This intentionally
         * contains only provider-manifest headers and no synthetic hunks.
         */
        public String maintenanceDiff() {
            StringBuilder metadataDiff = new StringBuilder();
            for (VcsPullRequestChangeManifest.Change change : fullManifest.changes()) {
                if (change == null || change.path().isBlank()) {
                    continue;
                }
                String oldPath = change.kind() == VcsPullRequestChangeManifest.ChangeKind.RENAMED
                        && !change.previousPath().isBlank()
                        ? change.previousPath()
                        : change.path();
                metadataDiff.append("diff --git a/")
                        .append(oldPath)
                        .append(" b/")
                        .append(change.path())
                        .append('\n');
                if (change.kind() == VcsPullRequestChangeManifest.ChangeKind.ADDED) {
                    metadataDiff.append("new file mode 100644\n");
                } else if (change.kind() == VcsPullRequestChangeManifest.ChangeKind.DELETED) {
                    metadataDiff.append("deleted file mode 100644\n");
                } else if (change.kind() == VcsPullRequestChangeManifest.ChangeKind.RENAMED) {
                    metadataDiff.append("rename from ").append(oldPath).append('\n')
                            .append("rename to ").append(change.path()).append('\n');
                }
            }
            if (metadataDiff.isEmpty() && fullManifest.isComplete()) {
                metadataDiff.append("# CodeCrow current-head context maintenance\n")
                        .append("# Complete provider manifest has no base-to-head paths\n");
            }
            return metadataDiff.toString();
        }

        public String selectedDiff() {
            return analysisMode == AnalysisMode.INCREMENTAL ? deltaDiff : fullDiff;
        }

        public List<String> fullChangedFiles() {
            return fullManifest.currentPaths();
        }

        public List<String> fullDeletedFiles() {
            return fullManifest.removedPaths();
        }
    }
}

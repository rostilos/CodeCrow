package org.rostilos.codecrow.analysisengine.util;

import java.nio.charset.StandardCharsets;
import java.util.List;

import org.rostilos.codecrow.analysisengine.exception.DiffTooLargeException;
import org.rostilos.codecrow.analysisengine.exception.DiffTooLargeException.LimitType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.AnalysisLimitsConfig;
import org.springframework.stereotype.Service;

/** Enforces explicitly configured spending limits; batching handles large diffs by default. */
@Service
public class AnalysisLimitEnforcer {
    public AnalysisLimitsConfig effectiveLimits(Project project) {
        AnalysisLimitsConfig deployment = new AnalysisLimitsConfig(
                envInt("ANALYSIS_MAX_FILES"),
                envLong("ANALYSIS_MAX_FILE_SIZE_BYTES"),
                envLong("ANALYSIS_MAX_TOTAL_DIFF_SIZE_BYTES"),
                envInt("ANALYSIS_MAX_TOTAL_TOKENS"));
        AnalysisLimitsConfig workspace = project.getWorkspace() != null
                ? project.getWorkspace().getAnalysisLimits() : null;
        AnalysisLimitsConfig projectLimits = project.getConfiguration() != null
                ? project.getConfiguration().analysisLimits() : null;
        return overlay(overlay(deployment, workspace), projectLimits);
    }

    public void enforce(Project project, Long pullRequestId, String rawDiff) {
        String scopedDiff = AnalysisScopeFilter.filterDiff(rawDiff, project);
        if (scopedDiff == null || scopedDiff.isBlank()) {
            return;
        }
        AnalysisLimitsConfig limits = effectiveLimits(project);
        List<DiffParsingUtils.FileChange> sections = DiffParsingUtils.parseFileChanges(scopedDiff);
        if (limits.maxFiles() != null && sections.size() > limits.maxFiles()) {
            throw exceeded(LimitType.FILES, sections.size(), limits.maxFiles(), project, pullRequestId, null);
        }

        long totalBytes = scopedDiff.getBytes(StandardCharsets.UTF_8).length;
        if (limits.maxTotalDiffSizeBytes() != null && totalBytes > limits.maxTotalDiffSizeBytes()) {
            throw exceeded(LimitType.TOTAL_DIFF_SIZE, totalBytes, limits.maxTotalDiffSizeBytes(),
                    project, pullRequestId, null);
        }

        for (DiffParsingUtils.FileChange section : sections) {
            long bytes = section.diff().getBytes(StandardCharsets.UTF_8).length;
            if (limits.maxFileSizeBytes() != null && bytes > limits.maxFileSizeBytes()) {
                throw exceeded(LimitType.FILE_SIZE, bytes, limits.maxFileSizeBytes(),
                        project, pullRequestId,
                        section.newPath() != null ? section.newPath() : section.oldPath());
            }
        }

        int estimatedTokens = TokenEstimator.estimateTokens(scopedDiff);
        if (limits.maxTotalTokens() != null && estimatedTokens > limits.maxTotalTokens()) {
            throw exceeded(LimitType.TOKENS, estimatedTokens, limits.maxTotalTokens(),
                    project, pullRequestId, null);
        }
    }

    private static DiffTooLargeException exceeded(LimitType type, long actual, long max,
            Project project, Long pullRequestId, String path) {
        return new DiffTooLargeException(type, actual, max, project.getId(), pullRequestId, path);
    }

    private static AnalysisLimitsConfig overlay(AnalysisLimitsConfig parent, AnalysisLimitsConfig child) {
        if (child == null) return parent;
        return new AnalysisLimitsConfig(
                child.maxFiles() != null ? child.maxFiles() : parent.maxFiles(),
                child.maxFileSizeBytes() != null ? child.maxFileSizeBytes() : parent.maxFileSizeBytes(),
                child.maxTotalDiffSizeBytes() != null ? child.maxTotalDiffSizeBytes() : parent.maxTotalDiffSizeBytes(),
                child.maxTotalTokens() != null ? child.maxTotalTokens() : parent.maxTotalTokens());
    }

    private static Integer envInt(String name) {
        try {
            int value = Integer.parseInt(System.getenv(name));
            return value > 0 ? value : null;
        }
        catch (NumberFormatException e) { return null; }
    }

    private static Long envLong(String name) {
        try {
            long value = Long.parseLong(System.getenv(name));
            return value > 0 ? value : null;
        }
        catch (NumberFormatException e) { return null; }
    }

}

package org.rostilos.codecrow.analysisengine.util;

import java.nio.charset.StandardCharsets;
import java.util.List;

import org.rostilos.codecrow.analysisengine.exception.DiffTooLargeException;
import org.rostilos.codecrow.analysisengine.exception.DiffTooLargeException.LimitType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.AnalysisLimitsConfig;
import org.springframework.stereotype.Service;

/** Enforces PR-wide hard limits before any enrichment or AI request is made. */
@Service
public class AnalysisLimitEnforcer {
    public static final int DEFAULT_MAX_FILES = 150;
    public static final long DEFAULT_MAX_FILE_SIZE_BYTES = 5L * 1024 * 1024;
    public static final long DEFAULT_MAX_TOTAL_DIFF_SIZE_BYTES = 20L * 1024 * 1024;
    public static final int DEFAULT_MAX_TOTAL_TOKENS = 1_000_000;

    public AnalysisLimitsConfig effectiveLimits(Project project) {
        AnalysisLimitsConfig deployment = new AnalysisLimitsConfig(
                envInt("ANALYSIS_MAX_FILES", DEFAULT_MAX_FILES),
                envLong("ANALYSIS_MAX_FILE_SIZE_BYTES", DEFAULT_MAX_FILE_SIZE_BYTES),
                envLong("ANALYSIS_MAX_TOTAL_DIFF_SIZE_BYTES", DEFAULT_MAX_TOTAL_DIFF_SIZE_BYTES),
                envInt("ANALYSIS_MAX_TOTAL_TOKENS", DEFAULT_MAX_TOTAL_TOKENS));
        AnalysisLimitsConfig workspace = project.getWorkspace() != null
                ? project.getWorkspace().getAnalysisLimits() : null;
        AnalysisLimitsConfig projectLimits = project.getConfiguration() != null
                ? project.getConfiguration().analysisLimits() : null;
        return overlay(overlay(deployment, workspace), projectLimits);
    }

    public void enforce(Project project, Long pullRequestId, String rawDiff) {
        if (rawDiff == null || rawDiff.isBlank()) {
            return;
        }
        AnalysisLimitsConfig limits = effectiveLimits(project);
        List<DiffParsingUtils.FileChange> sections = DiffParsingUtils.parseFileChanges(rawDiff);
        if (sections.size() > limits.maxFiles()) {
            throw exceeded(LimitType.FILES, sections.size(), limits.maxFiles(), project, pullRequestId, null);
        }

        long totalBytes = rawDiff.getBytes(StandardCharsets.UTF_8).length;
        if (totalBytes > limits.maxTotalDiffSizeBytes()) {
            throw exceeded(LimitType.TOTAL_DIFF_SIZE, totalBytes, limits.maxTotalDiffSizeBytes(),
                    project, pullRequestId, null);
        }

        for (DiffParsingUtils.FileChange section : sections) {
            long bytes = section.diff().getBytes(StandardCharsets.UTF_8).length;
            if (bytes > limits.maxFileSizeBytes()) {
                throw exceeded(LimitType.FILE_SIZE, bytes, limits.maxFileSizeBytes(),
                        project, pullRequestId,
                        section.newPath() != null ? section.newPath() : section.oldPath());
            }
        }

        int estimatedTokens = TokenEstimator.estimateTokens(rawDiff);
        if (estimatedTokens > limits.maxTotalTokens()) {
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

    private static int envInt(String name, int fallback) {
        try {
            int value = Integer.parseInt(System.getenv().getOrDefault(name, String.valueOf(fallback)));
            return value > 0 ? value : fallback;
        }
        catch (NumberFormatException e) { return fallback; }
    }

    private static long envLong(String name, long fallback) {
        try {
            long value = Long.parseLong(System.getenv().getOrDefault(name, String.valueOf(fallback)));
            return value > 0 ? value : fallback;
        }
        catch (NumberFormatException e) { return fallback; }
    }

}

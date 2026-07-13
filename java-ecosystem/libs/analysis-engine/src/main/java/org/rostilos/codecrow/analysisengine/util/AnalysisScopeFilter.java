package org.rostilos.codecrow.analysisengine.util;

import java.util.List;
import java.util.Set;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.AnalysisScopeConfig;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;

/** Applies project analysis include/exclude globs to unified diffs and file sets. */
public final class AnalysisScopeFilter {
    private AnalysisScopeFilter() {}

    public static AnalysisScopeConfig scope(Project project) {
        if (project == null) return new AnalysisScopeConfig();
        ProjectConfig config = project.getEffectiveConfig();
        return config != null ? config.analysisScope() : new AnalysisScopeConfig();
    }

    public static String filterDiff(String rawDiff, Project project) {
        if (rawDiff == null || rawDiff.isBlank()) return rawDiff;
        AnalysisScopeConfig scope = scope(project);
        if (scope.includePatterns().isEmpty() && scope.excludePatterns().isEmpty()) return rawDiff;

        StringBuilder result = new StringBuilder();
        List<DiffParsingUtils.FileChange> changes = DiffParsingUtils.parseFileChanges(rawDiff);
        for (DiffParsingUtils.FileChange change : changes) {
            if (scope.includesChange(change.oldPath(), change.newPath())) {
                result.append(change.diff());
            }
        }
        return result.toString();
    }

    public static void retainIncluded(Set<String> paths, Project project) {
        AnalysisScopeConfig scope = scope(project);
        paths.removeIf(path -> !scope.includes(path));
    }
}

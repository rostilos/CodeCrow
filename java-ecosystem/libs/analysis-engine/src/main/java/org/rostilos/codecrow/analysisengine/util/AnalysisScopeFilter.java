package org.rostilos.codecrow.analysisengine.util;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.plugins.RepositoryFileScope;
import org.rostilos.codecrow.core.model.project.config.AnalysisScopeConfig;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;

/**
 * Applies the effective analysis file scope to unified diffs and file sets.
 * Project exclusions are combined with generated artifacts that are never useful
 * review input. This is the shared scope for PR review, branch review,
 * reconciliation and hard-limit accounting.
 */
public final class AnalysisScopeFilter {
    public static final List<String> DEFAULT_EXCLUDE_PATTERNS = List.of(
            "*.min.js",
            "*.min.mjs",
            "*.min.cjs",
            "*.min.css",
            "*.js.map",
            "*.mjs.map",
            "*.cjs.map",
            "*.jsx.map",
            "*.ts.map",
            "*.tsx.map",
            "*.css.map",
            "*.scss.map",
            "*.sass.map",
            "*.less.map",
            "*.vue.map",
            "*.svelte.map",
            "*.dart.map",
            "*.wasm.map");

    private AnalysisScopeFilter() {}

    public static AnalysisScopeConfig scope(Project project) {
        AnalysisScopeConfig configured = new AnalysisScopeConfig();
        if (project != null) {
            ProjectConfig config = project.getEffectiveConfig();
            if (config != null && config.analysisScope() != null) {
                configured = config.analysisScope();
            }
        }

        List<String> effectiveExclusions = new ArrayList<>(
                DEFAULT_EXCLUDE_PATTERNS.size() + configured.excludePatterns().size());
        effectiveExclusions.addAll(DEFAULT_EXCLUDE_PATTERNS);
        effectiveExclusions.addAll(configured.excludePatterns());
        return new AnalysisScopeConfig(configured.includePatterns(), effectiveExclusions);
    }

    public static String filterDiff(String rawDiff, Project project) {
        return RepositoryFileScope.filterDiff(rawDiff, scope(project)::includesChange);
    }

    public static void retainIncluded(Set<String> paths, Project project) {
        AnalysisScopeConfig scope = scope(project);
        paths.removeIf(path -> !scope.includes(path));
    }
}

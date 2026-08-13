package org.rostilos.codecrow.analysisengine.util;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.AnalysisScopeConfig;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.vcsclient.model.VcsPullRequestChangeManifest;

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
        if (rawDiff == null || rawDiff.isBlank()) return rawDiff;
        AnalysisScopeConfig scope = scope(project);

        StringBuilder result = new StringBuilder();
        boolean excludedAny = false;
        List<DiffParsingUtils.FileChange> changes = DiffParsingUtils.parseFileChanges(rawDiff);
        for (DiffParsingUtils.FileChange change : changes) {
            if (scope.includesChange(change.oldPath(), change.newPath())) {
                result.append(change.diff());
            } else {
                excludedAny = true;
            }
        }
        return excludedAny ? result.toString() : rawDiff;
    }

    /**
     * Project a provider-complete PR inventory into the configured analysis
     * scope. Completeness is preserved because the result is complete within
     * that scope. A rename leaving the scope becomes an old-path tombstone so
     * a formerly indexed in-scope file cannot survive in the current overlay.
     */
    public static VcsPullRequestChangeManifest filterManifest(
            VcsPullRequestChangeManifest manifest,
            Project project) {
        if (manifest == null) {
            return VcsPullRequestChangeManifest.unavailable(
                    "provider manifest was null");
        }
        AnalysisScopeConfig scope = scope(project);
        List<VcsPullRequestChangeManifest.Change> retained = new ArrayList<>();
        boolean projected = false;
        for (VcsPullRequestChangeManifest.Change change : manifest.changes()) {
            if (change == null || change.path().isBlank()) {
                projected = true;
                continue;
            }
            if (change.kind() == VcsPullRequestChangeManifest.ChangeKind.RENAMED) {
                boolean currentIncluded = scope.includes(change.path());
                boolean previousIncluded = scope.includes(change.previousPath());
                if (currentIncluded && previousIncluded) {
                    retained.add(change);
                } else if (currentIncluded) {
                    retained.add(new VcsPullRequestChangeManifest.Change(
                            change.path(), "",
                            VcsPullRequestChangeManifest.ChangeKind.ADDED));
                    projected = true;
                } else if (previousIncluded) {
                    retained.add(new VcsPullRequestChangeManifest.Change(
                            change.previousPath(), "",
                            VcsPullRequestChangeManifest.ChangeKind.DELETED));
                    projected = true;
                } else {
                    projected = true;
                }
                continue;
            }
            if (scope.includes(change.path())) {
                retained.add(change);
            } else {
                projected = true;
            }
        }
        String receipt = manifest.receipt();
        if (projected) {
            receipt = (receipt.isBlank() ? "" : receipt + ";")
                    + "analysis-scope-projected";
        }
        return new VcsPullRequestChangeManifest(
                retained,
                manifest.completeness(),
                receipt);
    }

    public static void retainIncluded(Set<String> paths, Project project) {
        AnalysisScopeConfig scope = scope(project);
        paths.removeIf(path -> !scope.includes(path));
    }
}

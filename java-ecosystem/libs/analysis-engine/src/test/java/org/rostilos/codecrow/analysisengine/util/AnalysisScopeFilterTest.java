package org.rostilos.codecrow.analysisengine.util;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.AnalysisScopeConfig;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;

class AnalysisScopeFilterTest {
    @Test
    void keepsOnlyIncludedNonExcludedDiffSections() {
        ProjectConfig config = new ProjectConfig();
        config.setAnalysisScope(new AnalysisScopeConfig(List.of("src/**"), List.of("src/generated/**")));
        Project project = new Project();
        project.setConfiguration(config);
        String diff = section("src/App.java") + section("src/generated/Api.java") + section("docs/readme.md");

        String filtered = AnalysisScopeFilter.filterDiff(diff, project);

        assertThat(filtered).contains("src/App.java");
        assertThat(filtered).doesNotContain("src/generated/Api.java", "docs/readme.md");
    }

    @Test
    void returnsOriginalDiffWhenScopeIsEmpty() {
        Project project = new Project();
        String diff = section("src/App.java");

        assertThat(AnalysisScopeFilter.filterDiff(diff, project)).isEqualTo(diff);
    }

    @Test
    void excludesMinifiedAssetsAndSourceMapsByDefault() {
        Project project = new Project();
        String diff = section("src/App.java")
                + section("assets/level.map")
                + section("public/app.min.js")
                + section("public/module.min.mjs")
                + section("public/legacy.min.cjs")
                + section("public/styles.min.css")
                + section("public/app.js.map")
                + section("public/styles.css.map")
                + section("public/module.mjs.map")
                + section("public/legacy.cjs.map")
                + section("public/component.jsx.map")
                + section("public/component.ts.map")
                + section("public/component.tsx.map")
                + section("public/styles.scss.map")
                + section("public/styles.sass.map")
                + section("public/styles.less.map")
                + section("public/widget.vue.map")
                + section("public/widget.svelte.map")
                + section("public/app.dart.map")
                + section("public/module.wasm.map");

        String filtered = AnalysisScopeFilter.filterDiff(diff, project);

        assertThat(filtered).contains("src/App.java", "assets/level.map");
        assertThat(filtered).doesNotContain(
                "app.min.js",
                "module.min.mjs",
                "legacy.min.cjs",
                "styles.min.css",
                "app.js.map",
                "styles.css.map",
                "module.mjs.map",
                "legacy.cjs.map",
                "component.jsx.map",
                "component.ts.map",
                "component.tsx.map",
                "styles.scss.map",
                "styles.sass.map",
                "styles.less.map",
                "widget.vue.map",
                "widget.svelte.map",
                "app.dart.map",
                "module.wasm.map");
    }

    @Test
    void appliesDefaultAndProjectExclusionsToFileSets() {
        ProjectConfig config = new ProjectConfig();
        config.setAnalysisScope(new AnalysisScopeConfig(List.of(), List.of("vendor/**")));
        Project project = new Project();
        project.setConfiguration(config);
        Set<String> paths = new LinkedHashSet<>(List.of(
                "src/App.java",
                "vendor/library.js",
                "public/app.min.js",
                "public/app.js.map"));

        AnalysisScopeFilter.retainIncluded(paths, project);

        assertThat(paths).containsExactly("src/App.java");
    }

    private String section(String path) {
        return "diff --git a/" + path + " b/" + path + "\n"
                + "--- a/" + path + "\n+++ b/" + path + "\n@@ -1 +1 @@\n-old\n+new\n";
    }
}

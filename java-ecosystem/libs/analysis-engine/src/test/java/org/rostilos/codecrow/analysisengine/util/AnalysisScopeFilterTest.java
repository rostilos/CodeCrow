package org.rostilos.codecrow.analysisengine.util;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;

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

        assertThat(AnalysisScopeFilter.filterDiff(diff, project)).isSameAs(diff);
    }

    private String section(String path) {
        return "diff --git a/" + path + " b/" + path + "\n"
                + "--- a/" + path + "\n+++ b/" + path + "\n@@ -1 +1 @@\n-old\n+new\n";
    }
}

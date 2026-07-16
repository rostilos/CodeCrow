package org.rostilos.codecrow.core.model.project.config;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;

import org.junit.jupiter.api.Test;

class AnalysisScopeConfigTest {
    @Test
    void exclusionsOverrideInclusions() {
        AnalysisScopeConfig scope = new AnalysisScopeConfig(
                List.of("src/**"), List.of("src/generated/**"));

        assertThat(scope.includes("src/main/App.java")).isTrue();
        assertThat(scope.includes("src/generated/Api.java")).isFalse();
        assertThat(scope.includes("docs/readme.md")).isFalse();
    }

    @Test
    void supportsRagPatternConventions() {
        AnalysisScopeConfig unrestricted = new AnalysisScopeConfig();

        assertThat(matches("src/main/App.java", "*.java")).isTrue();
        assertThat(matches("vendor/package/file.php", "vendor/")).isTrue();
        assertThat(matches("App.java", "**/*.java")).isTrue();
        assertThat(matches("src/App.java", "**/*.java")).isTrue();
        assertThat(matches("src/App.java", "src/**/*.java")).isTrue();
        assertThat(matches("src/main/App.java", "src/**/*.java")).isTrue();
        assertThat(matches("src/nested/App.java", "src/*")).isFalse();
        assertThat(matches("src/nested/App.java", "src/**")).isTrue();
        assertThat(unrestricted.includes("any/path.txt")).isTrue();
    }

    @Test
    void changesUseDestinationScopeAndDeletionsUseOldPath() {
        AnalysisScopeConfig scope = new AnalysisScopeConfig(List.of("src/**"), List.of());

        assertThat(scope.includesChange("archive/App.java", "src/App.java")).isTrue();
        assertThat(scope.includesChange("src/Old.java", "archive/Old.java")).isFalse();
        assertThat(scope.includesChange("src/Deleted.java", null)).isTrue();
    }

    private boolean matches(String path, String pattern) {
        return new AnalysisScopeConfig(List.of(pattern), List.of()).includes(path);
    }
}

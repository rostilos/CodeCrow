package org.rostilos.codecrow.analysisengine.util;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.exception.DiffTooLargeException;
import org.rostilos.codecrow.analysisengine.exception.DiffTooLargeException.LimitType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.AnalysisLimitsConfig;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.workspace.Workspace;

class AnalysisLimitEnforcerTest {
    private final AnalysisLimitEnforcer enforcer = new AnalysisLimitEnforcer();

    @Test
    void projectOverridesWorkspaceAndDeploymentLimits() {
        Project project = projectWithLimits(
                new AnalysisLimitsConfig(10, 2_000L, 5_000L, 2_000),
                new AnalysisLimitsConfig(2, null, null, null));

        AnalysisLimitsConfig effective = enforcer.effectiveLimits(project);

        assertThat(effective.maxFiles()).isEqualTo(2);
        assertThat(effective.maxFileSizeBytes()).isEqualTo(2_000L);
        assertThat(effective.maxTotalDiffSizeBytes()).isEqualTo(5_000L);
        assertThat(effective.maxTotalTokens()).isEqualTo(2_000);
    }

    @Test
    void rejectsFileCountBeforeAnalysis() {
        Project project = projectWithLimits(
                AnalysisLimitsConfig.empty(),
                new AnalysisLimitsConfig(1, 10_000L, 20_000L, 10_000));
        String diff = """
                diff --git a/a.java b/a.java
                --- a/a.java
                +++ b/a.java
                @@ -1 +1 @@
                -old
                +new
                diff --git a/b.java b/b.java
                --- a/b.java
                +++ b/b.java
                @@ -1 +1 @@
                -old
                +new
                """;

        assertThatThrownBy(() -> enforcer.enforce(project, 42L, diff))
                .isInstanceOfSatisfying(DiffTooLargeException.class, error -> {
                    assertThat(error.getLimitType()).isEqualTo(LimitType.FILES);
                    assertThat(error.getActualValue()).isEqualTo(2);
                    assertThat(error.getMaxAllowedValue()).isEqualTo(1);
                });
    }

    @Test
    void reportsPathForOversizedSingleFileDiff() {
        Project project = projectWithLimits(
                AnalysisLimitsConfig.empty(),
                new AnalysisLimitsConfig(10, 60L, 20_000L, 10_000));
        String diff = """
                diff --git a/src/large.java b/src/large.java
                --- a/src/large.java
                +++ b/src/large.java
                @@ -1 +1 @@
                -some long original content
                +some long replacement content
                """;

        assertThatThrownBy(() -> enforcer.enforce(project, 42L, diff))
                .isInstanceOfSatisfying(DiffTooLargeException.class, error -> {
                    assertThat(error.getLimitType()).isEqualTo(LimitType.FILE_SIZE);
                    assertThat(error.getFilePath()).isEqualTo("src/large.java");
                });
    }

    private Project projectWithLimits(AnalysisLimitsConfig workspaceLimits, AnalysisLimitsConfig projectLimits) {
        Workspace workspace = new Workspace("test", "Test", null);
        workspace.setAnalysisLimits(workspaceLimits);
        ProjectConfig config = new ProjectConfig();
        config.setAnalysisLimits(projectLimits);
        Project project = new Project();
        project.setWorkspace(workspace);
        project.setConfiguration(config);
        return project;
    }
}

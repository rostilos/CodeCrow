package org.rostilos.codecrow.analysisengine.service.pr;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.exception.DiffTooLargeException;
import org.rostilos.codecrow.analysisengine.util.AnalysisLimitEnforcer;
import org.rostilos.codecrow.analysisengine.util.DiffContentFilter;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.AnalysisLimitsConfig;
import org.rostilos.codecrow.core.model.project.config.AnalysisScopeConfig;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;

class PullRequestDiffPreparationServiceTest {
    private final PullRequestDiffPreparationService service =
            new PullRequestDiffPreparationService(
                    new DiffContentFilter(), new AnalysisLimitEnforcer());

    @Test
    void isConstructedBySpringUsingItsProductionDependency() {
        try (AnnotationConfigApplicationContext context = new AnnotationConfigApplicationContext()) {
            context.register(AnalysisLimitEnforcer.class, PullRequestDiffPreparationService.class);
            context.refresh();

            assertThat(context.getBean(PullRequestDiffPreparationService.class)).isNotNull();
        }
    }

    @Test
    void appliesScopeAndExtractsFilesOnceForEveryProvider() {
        Project project = project(
                new AnalysisScopeConfig(List.of("src/**"), List.of("src/generated/**")),
                AnalysisLimitsConfig.empty());
        String diff = section("src/App.java", "changed")
                + section("src/generated/Api.java", "generated")
                + section("docs/readme.md", "docs");

        var prepared = service.prepare(project, 42L, diff, null, "head",
                (base, head) -> { throw new AssertionError("delta must not be fetched"); });

        assertThat(prepared.analysisMode()).isEqualTo(AnalysisMode.FULL);
        assertThat(prepared.changedFiles()).containsExactly("src/App.java");
        assertThat(prepared.fullDiff()).contains("src/App.java");
        assertThat(prepared.fullDiff()).doesNotContain("generated/Api.java", "docs/readme.md");
    }

    @Test
    void selectsAUsefulScopedIncrementalDiff() {
        Project project = project(new AnalysisScopeConfig(), AnalysisLimitsConfig.empty());
        String fullDiff = section("src/App.java", "x".repeat(1800));
        String deltaDiff = section("src/App.java", "y".repeat(600));

        var prepared = service.prepare(
                project, 42L, fullDiff, "base", "head", (base, head) -> deltaDiff);

        assertThat(prepared.analysisMode()).isEqualTo(AnalysisMode.INCREMENTAL);
        assertThat(prepared.selectedDiff()).isEqualTo(deltaDiff);
    }

    @Test
    void enforcesHardLimitsOnScopedContentBeforeSoftContentFiltering() {
        AnalysisLimitsConfig limits = new AnalysisLimitsConfig(10, 1_000L, 100_000L, 100_000);
        Project project = project(new AnalysisScopeConfig(), limits);
        String oversizedFile = section("src/Large.java", "x".repeat(30_000));

        assertThatThrownBy(() -> service.prepare(
                project, 42L, oversizedFile, null, "head", (base, head) -> null))
                .isInstanceOf(DiffTooLargeException.class)
                .hasMessageContaining("file_size");
    }

    @Test
    void excludedFilesDoNotConsumeAnalysisLimits() {
        AnalysisLimitsConfig limits = new AnalysisLimitsConfig(1, 1_000L, 2_000L, 1_000);
        Project project = project(
                new AnalysisScopeConfig(List.of("src/**"), List.of()), limits);
        String diff = section("vendor/Large.js", "x".repeat(30_000))
                + section("src/App.java", "small");

        var prepared = service.prepare(
                project, 42L, diff, null, "head", (base, head) -> null);

        assertThat(prepared.changedFiles()).containsExactly("src/App.java");
    }

    @Test
    void agenticExactRetainsLargeInScopeHunksInsteadOfAPlaceholder() {
        Project project = project(new AnalysisScopeConfig(), AnalysisLimitsConfig.empty());
        String largeHunk = "x".repeat(30_000);
        String exactDiff = section("src/Large.java", largeHunk);

        var prepared = service.prepareAgenticExact(
                project, 42L, exactDiff, "a".repeat(40), "b".repeat(40));

        assertThat(prepared.analysisMode()).isEqualTo(AnalysisMode.FULL);
        assertThat(prepared.changedFiles()).containsExactly("src/Large.java");
        assertThat(prepared.fullDiff())
                .contains("@@ -1 +1 @@", largeHunk)
                .doesNotContain("CodeCrow Filter");
    }

    @Test
    void agenticExactScopesAnUnquotedPathContainingSpaces() {
        Project project = project(
                new AnalysisScopeConfig(List.of("src/**"), List.of()),
                AnalysisLimitsConfig.empty());
        String diff = section("src/My File.java", "changed");

        var prepared = service.prepareAgenticExact(
                project, 42L, diff, "a".repeat(40), "b".repeat(40));

        assertThat(prepared.changedFiles()).containsExactly("src/My File.java");
        assertThat(prepared.fullDiff()).isEqualTo(diff);
    }

    @Test
    void agenticExactDecodesCQuotedUtf8PathsBeforeScoping() {
        Project project = project(
                new AnalysisScopeConfig(List.of("src/**"), List.of()),
                AnalysisLimitsConfig.empty());
        String encoded = "src/\\346\\227\\245\\346\\234\\254.java";
        String diff = "diff --git \"a/" + encoded + "\" \"b/" + encoded + "\"\n"
                + "--- \"a/" + encoded + "\"\n"
                + "+++ \"b/" + encoded + "\"\n"
                + "@@ -1 +1 @@\n-old\n+new\n";

        var prepared = service.prepareAgenticExact(
                project, 42L, diff, "a".repeat(40), "b".repeat(40));

        assertThat(prepared.changedFiles()).containsExactly("src/日本.java");
        assertThat(prepared.fullDiff()).isEqualTo(diff);
    }

    @Test
    void agenticExactFailsClosedForUnsectionedProviderContent() {
        Project project = project(new AnalysisScopeConfig(), AnalysisLimitsConfig.empty());

        assertThatThrownBy(() -> service.prepareAgenticExact(
                project, 42L, "provider returned no diff headers", "a".repeat(40), "b".repeat(40)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("first file header");
    }

    @Test
    void agenticExactSupportsMixedQuotedHeadersAndMarkerTimestamps() {
        Project project = project(
                new AnalysisScopeConfig(List.of("src/**"), List.of()),
                AnalysisLimitsConfig.empty());
        String diff = "diff --git a/src/My File.java \"b/src/My File.java\"\n"
                + "--- a/src/My File.java\t2026-01-01\n"
                + "+++ \"b/src/My File.java\"\t2026-01-01\n"
                + "@@ -1 +1 @@\n-old\n+new\n";

        var prepared = service.prepareAgenticExact(
                project, 42L, diff, "a".repeat(40), "b".repeat(40));

        assertThat(prepared.changedFiles()).containsExactly("src/My File.java");
    }

    @Test
    void agenticExactRejectsMalformedQuotedUtf8() {
        Project project = project(new AnalysisScopeConfig(), AnalysisLimitsConfig.empty());
        String diff = "diff --git \"a/src/\\377.java\" \"b/src/\\377.java\"\n"
                + "--- \"a/src/\\377.java\"\n+++ \"b/src/\\377.java\"\n"
                + "@@ -1 +1 @@\n-old\n+new\n";

        assertThatThrownBy(() -> service.prepareAgenticExact(
                project, 42L, diff, "a".repeat(40), "b".repeat(40)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Malformed UTF-8");
    }

    private Project project(AnalysisScopeConfig scope, AnalysisLimitsConfig limits) {
        ProjectConfig config = new ProjectConfig();
        config.setAnalysisScope(scope);
        config.setAnalysisLimits(limits);
        Project project = new Project();
        project.setConfiguration(config);
        return project;
    }

    private String section(String path, String addedContent) {
        return "diff --git a/" + path + " b/" + path + "\n"
                + "--- a/" + path + "\n+++ b/" + path + "\n@@ -1 +1 @@\n-old\n+"
                + addedContent + "\n";
    }
}

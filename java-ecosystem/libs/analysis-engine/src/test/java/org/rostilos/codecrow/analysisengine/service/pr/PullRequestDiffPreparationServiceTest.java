package org.rostilos.codecrow.analysisengine.service.pr;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.exception.DiffTooLargeException;
import org.rostilos.codecrow.analysisengine.util.AnalysisLimitEnforcer;
import org.rostilos.codecrow.analysisengine.util.AnalysisScopeFilter;
import org.rostilos.codecrow.analysisengine.util.VcsDiffUtils;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.AnalysisLimitsConfig;
import org.rostilos.codecrow.core.model.project.config.AnalysisScopeConfig;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.vcsclient.model.VcsPullRequestChangeManifest;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;

class PullRequestDiffPreparationServiceTest {
    private final PullRequestDiffPreparationService service =
            new PullRequestDiffPreparationService(new AnalysisLimitEnforcer());

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
        String fullDiff = section("src/First.java", "x".repeat(1200))
                + section("src/Second.java", "z".repeat(1200));
        String deltaDiff = section("src/Second.java", "y".repeat(600));
        var manifest = new VcsPullRequestChangeManifest(
                List.of(
                        change("src/First.java", VcsPullRequestChangeManifest.ChangeKind.ADDED),
                        change("src/Second.java", VcsPullRequestChangeManifest.ChangeKind.MODIFIED)),
                VcsPullRequestChangeManifest.Completeness.COMPLETE,
                "test:complete");

        var prepared = service.prepare(
                project, 42L, fullDiff, "base", "head", manifest,
                (base, head) -> deltaDiff);

        assertThat(prepared.analysisMode()).isEqualTo(AnalysisMode.INCREMENTAL);
        assertThat(prepared.selectedDiff()).isEqualTo(deltaDiff);
        assertThat(prepared.changedFiles()).containsExactly("src/Second.java");
        assertThat(prepared.fullChangedFiles())
                .containsExactly("src/First.java", "src/Second.java");
        assertThat(prepared.fullManifest().isComplete()).isTrue();
    }

    @Test
    void keepsARealSmallDeltaIncremental() {
        Project project = project(new AnalysisScopeConfig(), AnalysisLimitsConfig.empty());
        String fullDiff = section("src/First.java", "x".repeat(1200))
                + section("src/Second.java", "z".repeat(1200));
        String deltaDiff = section("src/Second.java", "one-line-fix");

        var prepared = service.prepare(
                project, 42L, fullDiff, "base", "head",
                VcsPullRequestChangeManifest.unavailable("test fallback"),
                (base, head) -> deltaDiff);

        assertThat(deltaDiff.length()).isLessThan(VcsDiffUtils.MIN_DELTA_DIFF_SIZE);
        assertThat(prepared.analysisMode()).isEqualTo(AnalysisMode.INCREMENTAL);
        assertThat(prepared.selectedDiff()).isEqualTo(deltaDiff);
        assertThat(prepared.changedFiles()).containsExactly("src/Second.java");
    }

    @Test
    void projectsCompleteManifestIntoAnalysisScope() {
        Project project = project(
                new AnalysisScopeConfig(List.of("src/**"), List.of()),
                AnalysisLimitsConfig.empty());
        String diff = section("src/App.java", "reviewable")
                + section("docs/Guide.md", "context only");
        var manifest = new VcsPullRequestChangeManifest(
                List.of(
                        change("src/App.java", VcsPullRequestChangeManifest.ChangeKind.MODIFIED),
                        change("docs/Guide.md", VcsPullRequestChangeManifest.ChangeKind.MODIFIED)),
                VcsPullRequestChangeManifest.Completeness.COMPLETE,
                "provider:page=1");

        var prepared = service.prepare(
                project, 42L, diff, null, "head", manifest, (base, head) -> null);

        assertThat(prepared.changedFiles()).containsExactly("src/App.java");
        assertThat(prepared.fullChangedFiles())
                .containsExactly("src/App.java");
        assertThat(prepared.fullManifest().isComplete()).isTrue();
        assertThat(prepared.fullManifest().receipt())
                .isEqualTo("provider:page=1;analysis-scope-projected");
    }

    @Test
    void projectsRenamesAtBothAnalysisScopeBoundaries() {
        Project project = project(
                new AnalysisScopeConfig(List.of("src/**"), List.of()),
                AnalysisLimitsConfig.empty());
        var entering = new VcsPullRequestChangeManifest.Change(
                "src/Entered.java", "docs/Old.md",
                VcsPullRequestChangeManifest.ChangeKind.RENAMED);
        var leaving = new VcsPullRequestChangeManifest.Change(
                "docs/New.md", "src/Left.java",
                VcsPullRequestChangeManifest.ChangeKind.RENAMED);
        var manifest = new VcsPullRequestChangeManifest(
                List.of(entering, leaving),
                VcsPullRequestChangeManifest.Completeness.COMPLETE,
                "provider:complete");

        var projected = AnalysisScopeFilter.filterManifest(manifest, project);

        assertThat(projected.changes()).containsExactly(
                new VcsPullRequestChangeManifest.Change(
                        "src/Entered.java", "",
                        VcsPullRequestChangeManifest.ChangeKind.ADDED),
                new VcsPullRequestChangeManifest.Change(
                        "src/Left.java", "",
                        VcsPullRequestChangeManifest.ChangeKind.DELETED));
        assertThat(projected.receipt())
                .isEqualTo("provider:complete;analysis-scope-projected");
    }

    @Test
    void keepsManifestMaintenanceWhenNoPathsAreReviewable() {
        Project project = project(
                new AnalysisScopeConfig(List.of("src/**"), List.of()),
                AnalysisLimitsConfig.empty());
        var manifest = new VcsPullRequestChangeManifest(
                List.of(change("docs/Guide.md", VcsPullRequestChangeManifest.ChangeKind.MODIFIED)),
                VcsPullRequestChangeManifest.Completeness.COMPLETE,
                "provider:complete");

        var prepared = service.prepare(
                project, 42L, section("docs/Guide.md", "only docs"),
                null, "head", manifest, (base, head) -> null);

        assertThat(prepared.hasReviewableDiff()).isFalse();
        assertThat(prepared.isEmpty()).isFalse();
        assertThat(prepared.fullChangedFiles()).isEmpty();
        assertThat(prepared.maintenanceDiff())
                .contains("Complete provider manifest has no base-to-head paths")
                .doesNotContain("docs/Guide.md", "@@");
    }

    @Test
    void completeEmptyManifestStillEmitsMaintenanceForRevertedSnapshotPruning() {
        Project project = project(new AnalysisScopeConfig(), AnalysisLimitsConfig.empty());
        var manifest = new VcsPullRequestChangeManifest(
                List.of(),
                VcsPullRequestChangeManifest.Completeness.COMPLETE,
                "provider:complete-empty");

        var prepared = service.prepare(
                project, 42L, "", null, "head", manifest, (base, head) -> null);

        assertThat(prepared.hasReviewableDiff()).isFalse();
        assertThat(prepared.isEmpty()).isFalse();
        assertThat(prepared.fullChangedFiles()).isEmpty();
        assertThat(prepared.fullDeletedFiles()).isEmpty();
        assertThat(prepared.maintenanceDiff())
                .isNotBlank()
                .contains("Complete provider manifest has no base-to-head paths");
    }

    @Test
    void successfulDeltaWithOnlyExcludedPathsDoesNotReReviewFullDiff() {
        Project project = project(
                new AnalysisScopeConfig(List.of("src/**"), List.of()),
                AnalysisLimitsConfig.empty());
        String fullDiff = section("src/OldChange.java", "old reviewable hunk")
                + section("docs/NewGuide.md", "new excluded hunk");
        String newestDelta = section("docs/NewGuide.md", "new excluded hunk");
        var manifest = new VcsPullRequestChangeManifest(
                List.of(
                        change("src/OldChange.java", VcsPullRequestChangeManifest.ChangeKind.MODIFIED),
                        change("docs/NewGuide.md", VcsPullRequestChangeManifest.ChangeKind.ADDED)),
                VcsPullRequestChangeManifest.Completeness.COMPLETE,
                "provider:complete");

        var prepared = service.prepare(
                project, 42L, fullDiff, "previous", "head", manifest,
                (base, head) -> newestDelta);

        assertThat(prepared.analysisMode()).isEqualTo(AnalysisMode.INCREMENTAL);
        assertThat(prepared.hasReviewableDiff()).isFalse();
        assertThat(prepared.changedFiles()).isEmpty();
        assertThat(prepared.fullChangedFiles())
                .containsExactly("src/OldChange.java");
        assertThat(prepared.maintenanceDiff())
                .contains("src/OldChange.java")
                .doesNotContain("docs/NewGuide.md")
                .doesNotContain("@@", "old reviewable hunk");
    }

    @Test
    void anIncompleteManifestStaysIncompleteWhenPatchAddsPaths() {
        Project project = project(new AnalysisScopeConfig(), AnalysisLimitsConfig.empty());
        var partial = VcsPullRequestChangeManifest.incomplete(
                List.of(change("src/App.java", VcsPullRequestChangeManifest.ChangeKind.MODIFIED)),
                "provider:truncated");

        var prepared = service.prepare(
                project, 42L,
                section("src/App.java", "one") + section("src/Other.java", "two"),
                null, "head", partial, (base, head) -> null);

        assertThat(prepared.fullChangedFiles())
                .containsExactly("src/App.java", "src/Other.java");
        assertThat(prepared.fullManifest().isComplete()).isFalse();
        assertThat(prepared.fullManifest().receipt())
                .contains("provider:truncated", "unified-diff-fallback:incomplete");
    }

    @Test
    void renameSourceAndDeletedPathsBecomeFullOverlayTombstones() {
        Project project = project(new AnalysisScopeConfig(), AnalysisLimitsConfig.empty());
        var manifest = new VcsPullRequestChangeManifest(
                List.of(
                        new VcsPullRequestChangeManifest.Change(
                                "src/New.java", "src/Old.java",
                                VcsPullRequestChangeManifest.ChangeKind.RENAMED),
                        change("src/Removed.java", VcsPullRequestChangeManifest.ChangeKind.DELETED)),
                VcsPullRequestChangeManifest.Completeness.COMPLETE,
                "provider:complete");

        var prepared = service.prepare(
                project, 42L, section("src/New.java", "renamed"),
                null, "head", manifest, (base, head) -> null);

        assertThat(prepared.fullChangedFiles()).containsExactly("src/New.java");
        assertThat(prepared.fullDeletedFiles())
                .containsExactly("src/Old.java", "src/Removed.java");
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

    private VcsPullRequestChangeManifest.Change change(
            String path,
            VcsPullRequestChangeManifest.ChangeKind kind) {
        return new VcsPullRequestChangeManifest.Change(path, "", kind);
    }
}

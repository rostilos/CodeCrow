package org.rostilos.codecrow.analysisengine.service;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.core.model.codeanalysis.*;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.reviewcheck.*;
import org.rostilos.codecrow.core.model.vcs.*;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.core.service.reviewcheck.ReviewCheckResultService;
import org.rostilos.codecrow.plugins.*;
import org.rostilos.codecrow.vcsclient.*;
import org.springframework.test.util.ReflectionTestUtils;

import java.io.IOException;
import java.util.*;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class ReviewCheckServiceTest {
    @ParameterizedTest @EnumSource(EVcsProvider.class)
    void threeReviewsKeepAdvisoriesOutsideOrdinaryIssuesAndAiHistory(EVcsProvider provider) throws Exception {
        var fixture = new Fixture(provider);
        var history = new ArrayList<CodeAnalysis>();
        for (int version = 1; version <= 3; version++) {
            var analysis = fixture.analysis(version);
            when(fixture.plugin.check(any())).thenReturn(new ReviewCheckPlugin.Result(
                    List.of(fixture.finding, fixture.finding), List.of(), true));
            fixture.service.run(fixture.project, fixture.request, analysis, fixture.events::add);
            assertThat(analysis.getIssues()).singleElement().satisfies(issue -> assertThat(issue.getTitle()).isEqualTo("Unsafe script execution"));
            var saved = fixture.saved.get(version - 1);
            assertThat(saved).singleElement().satisfies(result -> {
                assertThat(result.isComplete()).isTrue();
                assertThat(result.getFindings()).hasSize(1);
                assertThat((Object) result.getFindings().get(0)).isNotInstanceOf(CodeAnalysisIssue.class);
            });
            history.add(0, analysis);
            assertThat(AiAnalysisRequestImpl.builder().withAllPrAnalysesData(history).build().getPreviousCodeAnalysisIssues())
                    .allMatch(issue -> issue.title().equals("Unsafe script execution"));
            assertThat(AiAnalysisRequestImpl.builder().withPreviousAnalysisData(Optional.of(analysis)).build().getPreviousCodeAnalysisIssues()).hasSize(1);
        }
        verify(fixture.plugin, times(3)).check(any());
        verify(fixture.analyses, times(3)).saveAnalysis(any());
    }

    @Test
    void scopedPinnedSourceReadsCannotEscapeTheRepositoryOrRevision() throws Exception {
        var f = new Fixture(EVcsProvider.GITHUB);
        when(f.client.getFileContent("owner", "repo", "composer.lock", "head")).thenReturn("source");
        when(f.plugin.check(any())).thenAnswer(call -> {
            ReviewCheckPlugin.Context context = call.getArgument(0);
            assertThat(context.baseRevision()).isEqualTo("merge-base");
            assertThat(context.source().read("head", "composer.lock")).isEqualTo("source");
            assertThatThrownBy(() -> context.source().read("foreign", "composer.lock")).isInstanceOf(IOException.class);
            assertThatThrownBy(() -> context.source().read("head", "../other-project")).isInstanceOf(IOException.class);
            return new ReviewCheckPlugin.Result(List.of(f.finding), List.of("audited"), true);
        });
        f.service.run(f.project, f.request, f.analysis(1), f.events::add);
        verify(f.client).getMergeBase("owner", "repo", "base", "head");
        verify(f.client).getFileContent("owner", "repo", "composer.lock", "head");
        verifyNoMoreInteractions(f.client);
    }

    @Test
    void failedPartialDisabledAndRemovedChecksPreserveOneSeparateLastKnownFinding() throws Exception {
        var f = new Fixture(EVcsProvider.BITBUCKET_CLOUD);
        f.previous = List.of(f.prior());
        when(f.plugin.check(any())).thenThrow(new IOException("offline"))
                .thenReturn(new ReviewCheckPlugin.Result(List.of(f.finding), List.of(), false));
        f.service.run(f.project, f.request, f.analysis(2), f.events::add);
        f.service.run(f.project, f.request, f.analysis(3), f.events::add);
        f.config.setReviewChecks(Map.of("composer-audit", false));
        f.service.run(f.project, f.request, f.analysis(4), f.events::add);
        new ReviewCheckService(f.clients, f.results, f.analyses, List.of()).run(f.project, f.request, f.analysis(5), f.events::add);
        for (var saved : f.saved) assertThat(saved).singleElement().satisfies(result -> {
            assertThat(result.isComplete()).isFalse(); assertThat(result.getFindings()).hasSize(1);
            assertThat(result.getDiagnostics()).isNotEmpty();
        });
        verify(f.plugin, times(2)).check(any());
    }

    @Test
    void cleanResultClearsOnlyCheckFindingsAndAnOutageCannotResurrectAnOlderAdvisory() throws Exception {
        var f = new Fixture(EVcsProvider.GITLAB);
        f.previous = List.of(f.prior());
        when(f.request.getProposedTreeChangedFiles()).thenReturn(List.of("app.php"));
        when(f.plugin.check(any())).thenReturn(new ReviewCheckPlugin.Result(List.of(), List.of(), true))
                .thenThrow(new IOException("offline"));
        f.service.run(f.project, f.request, f.analysis(2), f.events::add);
        verify(f.plugin).check(argThat(context -> context.changedPaths().contains("composer.lock")));
        f.service.run(f.project, f.request, f.analysis(3), f.events::add);
        assertThat(f.saved).hasSize(2);
        assertThat(f.saved.get(0).get(0).getFindings()).isEmpty();
        assertThat(f.saved.get(1).get(0).getFindings()).isEmpty();
    }

    @Test
    void unrelatedReviewKeepsTheLastCleanResultWithoutAnIncompleteWarningOrWorkerCall() throws Exception {
        var f = new Fixture(EVcsProvider.GITHUB);
        var clean = f.prior(); clean.getFindings().clear(); f.previous = List.of(clean);
        when(f.request.getProposedTreeChangedFiles()).thenReturn(List.of("app.php"));
        when(f.plugin.appliesTo(List.of("app.php"))).thenReturn(false);
        f.service.run(f.project, f.request, f.analysis(2), f.events::add);
        assertThat(f.saved.get(0)).singleElement().satisfies(result -> {
            assertThat(result.isComplete()).isTrue(); assertThat(result.getFindings()).isEmpty();
            assertThat(result.getDiagnostics()).isEmpty();
        });
        verify(f.plugin, never()).check(any());
    }

    @Test
    void excludedInputsCannotClearKnownFindingsWhenAnotherRootIsChecked() throws Exception {
        var f = new Fixture(EVcsProvider.GITLAB);
        var prior = f.prior(); prior.getFindings().get(0).setFilePath("old/composer.lock");
        f.previous = List.of(prior);
        f.config.setAnalysisScope(new org.rostilos.codecrow.core.model.project.config.AnalysisScopeConfig(List.of(), List.of("old/")));
        when(f.request.getProposedTreeChangedFiles()).thenReturn(List.of("other/composer.lock"));
        when(f.plugin.check(any())).thenReturn(new ReviewCheckPlugin.Result(List.of(), List.of(), true));
        f.service.run(f.project, f.request, f.analysis(2), f.events::add);
        assertThat(f.saved.get(0).get(0).isComplete()).isFalse();
        assertThat(f.saved.get(0).get(0).getFindings()).hasSize(1);
    }

    @Test
    void manualDecisionSurvivesARepeatedAuditAndTheSameSavedAnalysisIsNotExecutedAgain() throws Exception {
        var f = new Fixture(EVcsProvider.GITHUB);
        var prior = f.prior(); prior.getFindings().get(0).setManualStatusResolved(true);
        prior.getFindings().get(0).setResolved(true); f.previous = List.of(prior);
        when(f.plugin.check(any())).thenReturn(new ReviewCheckPlugin.Result(List.of(f.finding), List.of(), true));
        var analysis = f.analysis(2);
        f.service.run(f.project, f.request, analysis, f.events::add);
        assertThat(f.saved.get(0).get(0).getFindings().get(0).isResolved()).isTrue();
        when(f.results.findForAnalysis(analysis)).thenReturn(f.saved.get(0));
        f.service.run(f.project, f.request, analysis, f.events::add);
        verify(f.plugin, times(1)).check(any());
        verify(f.results, times(1)).save(any(), any());
    }

    @Test
    void storageFailureAndCancellationDoNotRetryOrChangeCoreIssues() throws Exception {
        var f = new Fixture(EVcsProvider.GITHUB);
        when(f.plugin.check(any())).thenReturn(new ReviewCheckPlugin.Result(List.of(f.finding), List.of(), true));
        doThrow(new IllegalStateException("db unavailable")).when(f.results).save(any(), any());
        var analysis = f.analysis(1);
        assertThatCode(() -> f.service.run(f.project, f.request, analysis, f.events::add)).doesNotThrowAnyException();
        verify(f.plugin).check(any()); verify(f.results).save(any(), any());
        verifyNoInteractions(f.analyses);
        assertThat(analysis.getIssues()).hasSize(1);
        var cancelled = new Fixture(EVcsProvider.GITHUB);
        when(cancelled.plugin.check(any())).thenThrow(new InterruptedException());
        try {
            cancelled.service.run(cancelled.project, cancelled.request, cancelled.analysis(1), cancelled.events::add);
            assertThat(Thread.currentThread().isInterrupted()).isTrue();
            verify(cancelled.plugin).check(any());
        } finally { Thread.interrupted(); }
    }

    @Test
    void foreignAnalysisIsRejectedBeforeAnyReadOrExecution() throws Exception {
        var f = new Fixture(EVcsProvider.GITHUB);
        var analysis = f.analysis(1); analysis.setProject(mock(Project.class));
        f.service.run(f.project, f.request, analysis, f.events::add);
        verifyNoInteractions(f.results, f.clients);
        verify(f.plugin, never()).check(any());
    }

    private static class Fixture {
        final Project project = mock(Project.class, RETURNS_DEEP_STUBS);
        final ProjectConfig config = new ProjectConfig();
        final AiAnalysisRequest request = mock(AiAnalysisRequest.class);
        final VcsClientProvider clients = mock(VcsClientProvider.class);
        final VcsClient client = mock(VcsClient.class);
        final ReviewCheckPlugin plugin = mock(ReviewCheckPlugin.class);
        final ReviewCheckResultService results = mock(ReviewCheckResultService.class);
        final CodeAnalysisService analyses = mock(CodeAnalysisService.class);
        final ReviewCheckService service = new ReviewCheckService(clients, results, analyses, List.of(plugin));
        final ReviewCheckPlugin.Finding finding = new ReviewCheckPlugin.Finding("composer.lock", 3, "HIGH",
                "vendor/package: TEST-001", "Known dependency vulnerability", "Update", "version");
        final List<List<ReviewCheckResult>> saved = new ArrayList<>();
        final List<Map<String, Object>> events = new ArrayList<>();
        List<ReviewCheckResult> previous = List.of();

        Fixture(EVcsProvider provider) throws Exception {
            when(project.getId()).thenReturn(1L); when(project.getEffectiveConfig()).thenReturn(config);
            var repository = project.getEffectiveVcsRepoInfo();
            when(repository.getRepoWorkspace()).thenReturn("owner"); when(repository.getRepoSlug()).thenReturn("repo");
            when(repository.getVcsConnection().getProviderType()).thenReturn(provider);
            when(clients.getClient(repository.getVcsConnection())).thenReturn(client);
            when(client.getMergeBase("owner", "repo", "base", "head")).thenReturn("merge-base");
            when(request.getPullRequestId()).thenReturn(42L); when(request.getBaseCommitHash()).thenReturn("base");
            when(request.getCurrentCommitHash()).thenReturn("head"); when(request.getProposedTreeChangedFiles()).thenReturn(List.of("composer.lock"));
            when(plugin.descriptor()).thenReturn(new PluginDescriptor("composer-audit", PluginKind.DOMAIN, List.of(),
                    List.of(PluginCapability.REVIEW_CHECK), DetectionRules.empty(), Map.of()));
            when(plugin.appliesTo(anyList())).thenReturn(true); when(plugin.findingKey(any())).thenCallRealMethod();
            when(results.findPrevious(any())).thenAnswer(call -> previous);
            doAnswer(call -> { previous = call.getArgument(1); saved.add(previous); return null; }).when(results).save(any(), any());
        }
        CodeAnalysis analysis(int version) {
            var analysis = new CodeAnalysis(); ReflectionTestUtils.setField(analysis, "id", (long) version);
            analysis.setProject(project); analysis.setPrNumber(42L); analysis.setPrVersion(version);
            var ordinary = new CodeAnalysisIssue(); ordinary.setTitle("Unsafe script execution"); ordinary.setFilePath("composer.json");
            ordinary.setReason("Untrusted script execution"); ordinary.setSeverity(IssueSeverity.HIGH); ordinary.setIssueCategory(IssueCategory.SECURITY);
            analysis.addIssue(ordinary); return analysis;
        }
        ReviewCheckResult prior() {
            var result = new ReviewCheckResult(); result.setCheckId("composer-audit"); result.setComplete(true);
            var entry = new ReviewCheckFinding(); entry.setFindingKey(ReviewCheckFinding.key(plugin.findingKey(finding)));
            entry.setFilePath(finding.file()); entry.setTitle(finding.title()); entry.setSeverity(IssueSeverity.HIGH);
            result.addFinding(entry); return result;
        }
    }
}

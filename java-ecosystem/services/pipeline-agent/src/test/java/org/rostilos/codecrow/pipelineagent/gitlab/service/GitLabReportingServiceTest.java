package org.rostilos.codecrow.pipelineagent.gitlab.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.springframework.test.util.ReflectionTestUtils;
import java.util.Map;
import static org.assertj.core.api.Assertions.*;
import org.junit.jupiter.params.provider.ValueSource;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.IssueAnchorSide;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsRepoBindingRepository;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.AnalysisSummary;
import org.rostilos.codecrow.vcsclient.bitbucket.service.ReportGenerator;
import org.rostilos.codecrow.vcsclient.gitlab.GitLabClient;

import java.io.IOException;
import java.util.List;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class GitLabReportingServiceTest {
    private static final String SUMMARY = "<!-- codecrow-analysis-comment -->";

    @ParameterizedTest
    @ValueSource(strings = {"existing", "placeholder", "new", "lost-update", "lost-create", "read-failure"})
    void mainSummaryIsReplacedBeforeDuplicatesAreRemovedAndWritesAreNeverRetried(String scenario) throws Exception {
        var client = mock(GitLabClient.class);
        var repository = mock(VcsRepoInfo.class);
        when(repository.getRepoWorkspace()).thenReturn("tenant");
        when(repository.getRepoSlug()).thenReturn("repo");
        var service = new GitLabReportingService(mock(ReportGenerator.class), mock(VcsClientProvider.class), mock(VcsRepoBindingRepository.class));
        List<Map<String, Object>> notes = List.of(
                Map.of("id", 10L, "body", SUMMARY + "old"), Map.of("id", 20L, "body", SUMMARY + "duplicate"),
                Map.of("id", 30L, "body", SUMMARY + "legacy inline", "type", "DiffNote"),
                Map.of("id", 700L, "body", "<!-- codecrow-review-checks -->dependencies"),
                Map.of("id", 800L, "body", "Human reply"));
        when(client.listMergeRequestNotes("tenant", "repo", 42L)).thenReturn(
                scenario.equals("new") || scenario.equals("lost-create") ? List.of() : notes);
        if (scenario.equals("read-failure"))
            when(client.listMergeRequestNotes("tenant", "repo", 42L)).thenThrow(new IOException("unavailable"));
        if (scenario.equals("lost-update"))
            doThrow(new IOException("accepted update, response lost")).when(client)
                    .updateMergeRequestNote(eq("tenant"), eq("repo"), eq(42L), eq(10L), anyString());
        if (scenario.equals("lost-create"))
            doThrow(new IOException("accepted creation, response lost")).when(client)
                    .postMergeRequestComment(eq("tenant"), eq("repo"), eq(42L), anyString());
        Runnable publish = () -> ReflectionTestUtils.invokeMethod(service, "postOrUpdateComment", client,
                repository, 42L, "fresh result", scenario.equals("placeholder") ? "99" : null);
        if (scenario.startsWith("lost-") || scenario.equals("read-failure"))
            assertThatThrownBy(publish::run).hasRootCauseInstanceOf(IOException.class);
        else publish.run();
        var order = inOrder(client);
        order.verify(client).listMergeRequestNotes("tenant", "repo", 42L);
        if (scenario.equals("new") || scenario.equals("lost-create"))
            order.verify(client).postMergeRequestComment("tenant", "repo", 42L, SUMMARY + "\nfresh result");
        else if (!scenario.equals("read-failure"))
            order.verify(client).updateMergeRequestNote("tenant", "repo", 42L,
                    scenario.equals("placeholder") ? 99L : 10L, SUMMARY + "\nfresh result");
        if (scenario.equals("placeholder")) order.verify(client).deleteMergeRequestNote("tenant", "repo", 42L, 10L);
        if (scenario.equals("existing") || scenario.equals("placeholder"))
            order.verify(client).deleteMergeRequestNote("tenant", "repo", 42L, 20L);
        verifyNoMoreInteractions(client);
    }

    @Test
    void resolvedReviewRemovesLegacyAndCurrentInlineNotesButPreservesHumanReplies() throws Exception {
        var client = mock(GitLabClient.class);
        var repository = mock(VcsRepoInfo.class);
        when(repository.getRepoWorkspace()).thenReturn("tenant");
        when(repository.getRepoSlug()).thenReturn("repo");
        var summary = AnalysisSummary.builder().withIssues(List.of()).build();
        when(client.listMergeRequestDiscussions("tenant", "repo", 42L)).thenReturn(List.of(new ObjectMapper().readTree("""
                {"id":"thread1","notes":[
                  {"id":10,"type":"DiffNote","body":"<!-- codecrow-analysis-comment -->old"},
                  {"id":11,"type":"DiscussionNote","body":"Human reply"},
                  {"id":12,"type":"DiffNote","body":"<!-- codecrow-inline-issue -->old"},
                  {"id":13,"body":"<!-- codecrow-analysis-comment -->main summary"}]}
                """)));
        var service = new GitLabReportingService(mock(ReportGenerator.class), mock(VcsClientProvider.class), mock(VcsRepoBindingRepository.class));
        ReflectionTestUtils.invokeMethod(service, "postInlineComments", client, repository, 42L, mock(CodeAnalysis.class), summary);
        verify(client).listMergeRequestDiscussions("tenant", "repo", 42L);
        verify(client).deleteMergeRequestDiscussionNote("tenant", "repo", 42L, "thread1", 10L);
        verify(client).deleteMergeRequestDiscussionNote("tenant", "repo", 42L, "thread1", 12L);
        verifyNoMoreInteractions(client);
    }

    @ParameterizedTest
    @ValueSource(strings = {"new", "existing", "resolved", "read-failure", "lost-response"})
    void updatesOneCheckSummaryWithoutRetryingAnUncertainWrite(String scenario) throws Exception {
        var client = mock(GitLabClient.class);
        var repository = mock(VcsRepoInfo.class);
        when(repository.getRepoWorkspace()).thenReturn("tenant");
        when(repository.getRepoSlug()).thenReturn("repo");
        String marker = "<!-- codecrow-review-checks -->";
        when(client.findMergeRequestNoteByMarker("tenant", "repo", 42L, marker)).thenReturn(null);
        if (scenario.equals("existing") || scenario.equals("resolved")) {
            when(client.findMergeRequestNoteByMarker("tenant", "repo", 42L, marker)).thenReturn(700L);
        } else if (scenario.equals("read-failure")) {
            when(client.findMergeRequestNoteByMarker("tenant", "repo", 42L, marker)).thenThrow(new IOException("Unavailable"));
        } else if (scenario.equals("lost-response")) {
            doThrow(new IOException("Response lost after write")).when(client)
                    .postMergeRequestComment(eq("tenant"), eq("repo"), eq(42L), anyString());
        }
        var service = new GitLabReportingService(mock(ReportGenerator.class), mock(VcsClientProvider.class), mock(VcsRepoBindingRepository.class));
        org.springframework.test.util.ReflectionTestUtils.invokeMethod(service, "postReviewCheckSummary",
                client, repository, 42L, scenario.equals("resolved") ? "" : "### Composer audit\n20 advisories");
        verify(client).findMergeRequestNoteByMarker("tenant", "repo", 42L, marker);
        switch (scenario) {
            case "existing" -> verify(client).updateMergeRequestNote(eq("tenant"), eq("repo"), eq(42L), eq(700L), contains(marker));
            case "resolved" -> verify(client).deleteMergeRequestNote("tenant", "repo", 42L, 700L);
            case "new", "lost-response" -> verify(client).postMergeRequestComment(eq("tenant"), eq("repo"), eq(42L), contains(marker));
        }
        verifyNoMoreInteractions(client);
    }

    @ParameterizedTest
    @ValueSource(booleans = {false, true})
    void composerAnchorsReachDiscussionsAndRejectedWritesAreNotRetried(boolean rejectManifest) throws Exception {
        var reports = mock(ReportGenerator.class);
        var clients = mock(VcsClientProvider.class);
        var client = mock(GitLabClient.class);
        var project = mock(Project.class);
        var repository = mock(VcsRepoInfo.class);
        var connection = mock(VcsConnection.class);
        var analysis = mock(CodeAnalysis.class);
        var summary = mock(AnalysisSummary.class);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repository);
        when(repository.getVcsConnection()).thenReturn(connection);
        when(repository.getRepoWorkspace()).thenReturn("tenant");
        when(repository.getRepoSlug()).thenReturn("repo");
        when(clients.getClient(connection)).thenReturn(client);
        when(reports.createAnalysisSummary(analysis, 77L)).thenReturn(summary);
        when(reports.createMarkdownSummary(analysis, summary, true)).thenReturn("summary");
        when(summary.getIssues()).thenReturn(List.of(
                new AnalysisSummary.IssueSummary(IssueSeverity.HIGH, "SECURITY", "composer.json", 3,
                        "phpmailer/phpmailer: TEST-001", "Known vulnerability", "Update", null,
                        null, 1L, "    \"phpmailer/phpmailer\": \"6.4.1\","),
                new AnalysisSummary.IssueSummary(IssueSeverity.HIGH, "SECURITY", "app/composer.lock", 7,
                        "a/change: TEST-002", "Known vulnerability", "Update", null,
                        null, 2L, "    \"version\": \"1.1.0\",")));
        when(client.getMergeRequest("tenant", "repo", 42L)).thenReturn(new ObjectMapper().readTree(
                "{\"diff_refs\":{\"base_sha\":\"base\",\"head_sha\":\"head\",\"start_sha\":\"start\"}}"));
        if (rejectManifest) {
            doThrow(new IOException("provider rejected line")).when(client).postMergeRequestLineComment(
                    eq("tenant"), eq("repo"), eq(42L), anyString(), eq("base"), eq("head"), eq("start"),
                    eq("composer.json"), eq(3), eq(IssueAnchorSide.HEAD));
        }

        new GitLabReportingService(reports, clients, mock(VcsRepoBindingRepository.class))
                .postAnalysisResults(analysis, project, 42L, 77L, "99");

        verify(client).postMergeRequestLineComment(eq("tenant"), eq("repo"), eq(42L), contains("TEST-001"),
                eq("base"), eq("head"), eq("start"), eq("composer.json"), eq(3), eq(IssueAnchorSide.HEAD));
        verify(client).postMergeRequestLineComment(eq("tenant"), eq("repo"), eq(42L), contains("TEST-002"),
                eq("base"), eq("head"), eq("start"), eq("app/composer.lock"), eq(7), eq(IssueAnchorSide.HEAD));
    }
}

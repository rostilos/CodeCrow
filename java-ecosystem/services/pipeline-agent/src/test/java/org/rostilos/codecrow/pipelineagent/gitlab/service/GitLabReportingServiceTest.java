package org.rostilos.codecrow.pipelineagent.gitlab.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.params.ParameterizedTest;
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

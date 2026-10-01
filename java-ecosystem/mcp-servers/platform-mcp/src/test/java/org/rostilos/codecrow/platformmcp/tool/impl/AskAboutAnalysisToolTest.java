package org.rostilos.codecrow.platformmcp.tool.impl;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.platformmcp.service.PlatformApiService;

import java.io.IOException;
import java.util.List;
import java.util.Map;
import java.util.stream.IntStream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class AskAboutAnalysisToolTest {

    @Test
    @SuppressWarnings("unchecked")
    void boundsRelatedIssuesAndReportsTheCompleteAcquisitionCount() throws Exception {
        PlatformApiService apiService = mock(PlatformApiService.class);
        List<Map<String, Object>> relatedIssues = IntStream.range(0, 31)
                .mapToObj(index -> Map.<String, Object>of("title", "issue-" + index))
                .toList();
        when(apiService.getAnalysisById(41L)).thenReturn(Map.of(
                "analysisId", 41L,
                "issuesComplete", true));
        when(apiService.searchIssues("HIGH", "SECURITY", null)).thenReturn(relatedIssues);

        Object rawResult = new AskAboutAnalysisTool(apiService).execute(Map.of(
                "analysisId", 41L,
                "question", "Which high security findings matter?"));

        Map<String, Object> result = (Map<String, Object>) rawResult;
        assertThat(result)
                .containsEntry("issueCount", 10)
                .containsEntry("relatedIssueTotalCount", 31)
                .containsEntry("omittedRelatedIssueCount", 21)
                .containsEntry("relatedIssuesComplete", false)
                .containsEntry("relatedIssuesStatus", "bounded_for_mcp_prompt")
                .containsKey("relatedIssuesDiagnostic");
        assertThat((List<Map<String, Object>>) result.get("relatedIssues")).hasSize(10);
        verify(apiService).searchIssues("HIGH", "SECURITY", null);
    }

    @Test
    @SuppressWarnings("unchecked")
    void boundsAnalysisIssuesWithoutChangingTheCompleteInternalResponse() throws Exception {
        PlatformApiService apiService = mock(PlatformApiService.class);
        List<Map<String, Object>> analysisIssues = IntStream.range(0, 73)
                .mapToObj(index -> Map.<String, Object>of("title", "analysis-" + index))
                .toList();
        when(apiService.getAnalysisById(41L)).thenReturn(Map.of(
                "analysisId", 41L,
                "issues", analysisIssues,
                "issueCount", 73,
                "issuesComplete", true));
        when(apiService.searchIssues(null, null, null)).thenReturn(List.of());

        Map<String, Object> result = (Map<String, Object>)
                new AskAboutAnalysisTool(apiService).execute(Map.of(
                        "analysisId", 41L,
                        "question", "What findings are there?"));

        Map<String, Object> analysis = (Map<String, Object>) result.get("analysis");
        assertThat((List<Map<String, Object>>) analysis.get("issues")).hasSize(50);
        assertThat(analysis)
                .containsEntry("issueCount", 50)
                .containsEntry("totalIssueCount", 73)
                .containsEntry("omittedIssueCount", 23)
                .containsEntry("issuesComplete", false)
                .containsEntry("issuesStatus", "bounded_for_mcp_prompt")
                .containsKey("issuesDiagnostic");
        assertThat(analysisIssues).hasSize(73);
    }

    @Test
    @SuppressWarnings("unchecked")
    void reportsUnavailableSearchWithoutPresentingItAsNoMatches() throws Exception {
        PlatformApiService apiService = mock(PlatformApiService.class);
        when(apiService.getAnalysisById(41L)).thenReturn(Map.of("analysisId", 41L));
        when(apiService.searchIssues(null, null, null))
                .thenThrow(new IOException("temporary API failure"));

        Object rawResult = new AskAboutAnalysisTool(apiService).execute(Map.of(
                "analysisId", 41L,
                "question", "What findings are there?"));

        Map<String, Object> result = (Map<String, Object>) rawResult;
        assertThat(result)
                .containsEntry("relatedIssuesComplete", false)
                .containsEntry("relatedIssuesStatus", "unavailable")
                .containsKey("relatedIssuesDiagnostic")
                .doesNotContainKeys("relatedIssues", "issueCount");
    }
}

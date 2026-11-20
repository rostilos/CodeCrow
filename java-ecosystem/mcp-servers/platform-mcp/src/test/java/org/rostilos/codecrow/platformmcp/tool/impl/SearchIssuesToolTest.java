package org.rostilos.codecrow.platformmcp.tool.impl;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.platformmcp.service.PlatformApiService;

import java.util.List;
import java.util.Map;
import java.util.stream.IntStream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class SearchIssuesToolTest {

    @Test
    @SuppressWarnings("unchecked")
    void boundsTheMcpProjectionAndReportsOmittedIssues() throws Exception {
        PlatformApiService apiService = mock(PlatformApiService.class);
        List<Map<String, Object>> issues = IntStream.range(0, 247)
                .mapToObj(index -> Map.<String, Object>of(
                        "title", "issue-" + index,
                        "suggestedFixDiff", "diff-" + index))
                .toList();
        when(apiService.getProjectId()).thenReturn(17L);
        when(apiService.searchIssues("HIGH", "SECURITY", null)).thenReturn(issues);

        Object rawResult = new SearchIssuesTool(apiService).execute(Map.of(
                "severity", "HIGH",
                "category", "SECURITY",
                "limit", 999));

        Map<String, Object> result = (Map<String, Object>) rawResult;
        assertThat(result)
                .containsEntry("count", 200)
                .containsEntry("totalCount", 247)
                .containsEntry("omittedIssueCount", 47)
                .containsEntry("issuesComplete", false)
                .containsEntry("issuesStatus", "bounded_for_mcp_prompt")
                .containsKey("issuesDiagnostic");
        List<Map<String, Object>> returnedIssues =
                (List<Map<String, Object>>) result.get("issues");
        assertThat(returnedIssues).hasSize(200);
        assertThat(returnedIssues.get(199))
                .containsEntry("suggestedFixDiff", "diff-199");
        verify(apiService).searchIssues("HIGH", "SECURITY", null);
    }
}

package org.rostilos.codecrow.pipelineagent.github.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.pullrequest.PullRequestState;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

class GitHubOperationsServiceTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void shouldMapOpenPullRequestState() throws Exception {
        assertThat(GitHubOperationsService.mapPullRequestState(
                objectMapper.readTree("{\"state\":\"open\"}"), 1L))
                .contains(PullRequestState.OPEN);
    }

    @Test
    void shouldMapMergedPullRequestState() throws Exception {
        assertThat(GitHubOperationsService.mapPullRequestState(
                objectMapper.readTree("{\"state\":\"closed\",\"merged\":true}"), 1L))
                .contains(PullRequestState.MERGED);
    }

    @Test
    void shouldMapClosedUnmergedPullRequestStateToDeclined() throws Exception {
        assertThat(GitHubOperationsService.mapPullRequestState(
                objectMapper.readTree("{\"state\":\"closed\",\"merged\":false,\"merged_at\":null}"), 1L))
                .contains(PullRequestState.DECLINED);
    }

    @Test
    void shouldReturnEmptyForUnknownState() throws Exception {
        Optional<PullRequestState> result = GitHubOperationsService.mapPullRequestState(
                objectMapper.readTree("{\"state\":\"queued\"}"), 1L);

        assertThat(result).isEmpty();
    }
}

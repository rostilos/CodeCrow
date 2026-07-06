package org.rostilos.codecrow.pipelineagent.gitlab.service;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.pullrequest.PullRequestState;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

class GitLabOperationsServiceTest {

    @Test
    void shouldMapOpenedMergeRequestState() {
        assertThat(GitLabOperationsService.mapMergeRequestState("opened", 1L))
                .contains(PullRequestState.OPEN);
    }

    @Test
    void shouldMapMergedMergeRequestState() {
        assertThat(GitLabOperationsService.mapMergeRequestState("merged", 1L))
                .contains(PullRequestState.MERGED);
    }

    @Test
    void shouldMapClosedMergeRequestStateToDeclined() {
        assertThat(GitLabOperationsService.mapMergeRequestState("closed", 1L))
                .contains(PullRequestState.DECLINED);
    }

    @Test
    void shouldReturnEmptyForLockedState() {
        Optional<PullRequestState> result = GitLabOperationsService.mapMergeRequestState("locked", 1L);

        assertThat(result).isEmpty();
    }

    @Test
    void shouldReturnEmptyForUnknownState() {
        Optional<PullRequestState> result = GitLabOperationsService.mapMergeRequestState("weird", 1L);

        assertThat(result).isEmpty();
    }
}

package org.rostilos.codecrow.pipelineagent.bitbucket.service;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.pullrequest.PullRequestState;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

class BitbucketOperationsServiceTest {

    @Test
    void shouldMapOpenPullRequestState() {
        assertThat(BitbucketOperationsService.mapPullRequestState("OPEN", 1L))
                .contains(PullRequestState.OPEN);
    }

    @Test
    void shouldMapMergedPullRequestState() {
        assertThat(BitbucketOperationsService.mapPullRequestState("MERGED", 1L))
                .contains(PullRequestState.MERGED);
    }

    @Test
    void shouldMapDeclinedPullRequestState() {
        assertThat(BitbucketOperationsService.mapPullRequestState("DECLINED", 1L))
                .contains(PullRequestState.DECLINED);
    }

    @Test
    void shouldMapSupersededPullRequestStateToDeclined() {
        assertThat(BitbucketOperationsService.mapPullRequestState("SUPERSEDED", 1L))
                .contains(PullRequestState.DECLINED);
    }

    @Test
    void shouldReturnEmptyForUnknownState() {
        Optional<PullRequestState> result = BitbucketOperationsService.mapPullRequestState("WEIRD", 1L);

        assertThat(result).isEmpty();
    }
}

package org.rostilos.codecrow.vcsclient;

import org.junit.jupiter.api.Test;
import org.mockito.Answers;
import org.rostilos.codecrow.core.model.pullrequest.PullRequestState;
import org.rostilos.codecrow.vcsclient.model.VcsPullRequest;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class VcsClientPullRequestStateTest {

    @Test
    void mapsProviderPullRequestStatesAtTheSharedBoundary() throws Exception {
        VcsClient client = mock(VcsClient.class, Answers.CALLS_REAL_METHODS);

        when(client.getPullRequest("workspace", "repository", 1))
                .thenReturn(pullRequest("opened", false));
        when(client.getPullRequest("workspace", "repository", 2))
                .thenReturn(pullRequest("closed", true));
        when(client.getPullRequest("workspace", "repository", 3))
                .thenReturn(pullRequest("superseded", false));
        when(client.getPullRequest("workspace", "repository", 4))
                .thenReturn(pullRequest("unknown", false));

        assertThat(client.getPullRequestState("workspace", "repository", 1))
                .contains(PullRequestState.OPEN);
        assertThat(client.getPullRequestState("workspace", "repository", 2))
                .contains(PullRequestState.MERGED);
        assertThat(client.getPullRequestState("workspace", "repository", 3))
                .contains(PullRequestState.DECLINED);
        assertThat(client.getPullRequestState("workspace", "repository", 4))
                .isEmpty();
    }

    private static VcsPullRequest pullRequest(String state, boolean merged) {
        return new VcsPullRequest(
                1, null, null, null, null, null, null, state, merged, null);
    }
}

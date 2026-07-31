package org.rostilos.codecrow.mcp.gitlab;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.vcsclient.gitlab.GitLabClient;
import org.rostilos.codecrow.vcsclient.model.VcsPullRequest;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class GitLabMcpClientImplTest {

    @Test
    void delegatesReviewReadsToSharedGitLabClient() throws Exception {
        GitLabClient sharedClient = mock(GitLabClient.class);
        GitLabConfiguration configuration = new GitLabConfiguration(
                "token",
                "team",
                "repository",
                "17",
                "https://gitlab.example");
        when(sharedClient.getPullRequest("team", "repository", 17))
                .thenReturn(new VcsPullRequest(
                        17,
                        "Review title",
                        "Review description",
                        "feature",
                        "main",
                        "base",
                        "head",
                        "opened",
                        false,
                        null));
        when(sharedClient.getPullRequestDiff("team", "repository", 17))
                .thenReturn("diff --git a/A.java b/A.java\n");

        GitLabMcpClientImpl adapter = new GitLabMcpClientImpl(
                sharedClient, configuration, 0);

        assertThat(adapter.getPullRequestTitle()).isEqualTo("Review title");
        assertThat(adapter.getPullRequestDiff("team", "repository", "17"))
                .contains("A.java");
        verify(sharedClient).getPullRequest("team", "repository", 17);
        verify(sharedClient).getPullRequestDiff("team", "repository", 17);
    }
}

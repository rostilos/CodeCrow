package org.rostilos.codecrow.vcsclient.gitlab;

import okhttp3.OkHttpClient;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.vcsclient.gitlab.api.GitLabApiContext;
import org.rostilos.codecrow.vcsclient.model.VcsPullRequest;

import static org.assertj.core.api.Assertions.assertThat;

class GitLabClientTest {

    @Test
    void reviewOperationsUseConfiguredSelfManagedApiBase() throws Exception {
        try (MockWebServer gitLab = new MockWebServer()) {
            gitLab.enqueue(jsonResponse("""
                    {
                      "iid": 17,
                      "title": "Self-managed review",
                      "description": "Description",
                      "source_branch": "feature",
                      "target_branch": "main",
                      "state": "opened",
                      "web_url": "https://gitlab.example/team/repo/-/merge_requests/17",
                      "diff_refs": {
                        "base_sha": "base-sha",
                        "head_sha": "head-sha"
                      }
                    }
                    """));
            gitLab.enqueue(jsonResponse("""
                    [{
                      "old_path": "src/App.java",
                      "new_path": "src/App.java",
                      "diff": "@@ -1 +1 @@\\n-old\\n+new"
                    }]
                    """));
            gitLab.start();

            String instanceBase = gitLab.url("/nested/gitlab/").toString();
            GitLabClient client = new GitLabClient(new OkHttpClient(), instanceBase);

            VcsPullRequest pullRequest = client.getPullRequest("team", "repo", 17);
            String diff = client.getPullRequestDiff("team", "repo", 17);

            assertThat(pullRequest.title()).isEqualTo("Self-managed review");
            assertThat(pullRequest.baseCommit()).isEqualTo("base-sha");
            assertThat(pullRequest.headCommit()).isEqualTo("head-sha");
            assertThat(diff).contains("diff --git a/src/App.java b/src/App.java")
                    .contains("+new");

            RecordedRequest metadataRequest = gitLab.takeRequest();
            RecordedRequest diffRequest = gitLab.takeRequest();
            assertThat(metadataRequest.getPath())
                    .isEqualTo("/nested/gitlab/api/v4/projects/team%2Frepo/merge_requests/17");
            assertThat(diffRequest.getPath())
                    .isEqualTo("/nested/gitlab/api/v4/projects/team%2Frepo/merge_requests/17/diffs?page=1&per_page=100");
        }
    }

    @Test
    void oneArgumentConstructorRetainsGitLabCloudApiDefault() throws Exception {
        GitLabClient client = new GitLabClient(new OkHttpClient());
        var field = GitLabClient.class.getDeclaredField("api");
        field.setAccessible(true);

        assertThat(((GitLabApiContext) field.get(client)).apiBaseUrl())
                .isEqualTo(GitLabConfig.API_BASE);
    }

    @Test
    void sharedFactoryCreatesAuthorizedSelfManagedClient() throws Exception {
        try (MockWebServer gitLab = new MockWebServer()) {
            gitLab.enqueue(jsonResponse("{}"));
            gitLab.start();

            GitLabClient client = GitLabClientFactory.createWithAccessToken(
                    "self-managed-token",
                    gitLab.url("/gitlab").toString());

            assertThat(client.validateConnection()).isTrue();
            RecordedRequest request = gitLab.takeRequest();
            assertThat(request.getPath()).isEqualTo("/gitlab/api/v4/user");
            assertThat(request.getHeader("Authorization"))
                    .isEqualTo("Bearer self-managed-token");
        }
    }

    @Test
    void connectionInstanceResolutionPreservesLegacyCloudDefault() {
        VcsConnection legacyConnection = new VcsConnection();
        VcsConnection selfManagedConnection = new VcsConnection();
        selfManagedConnection.setConfiguration(
                new org.rostilos.codecrow.core.model.vcs.config.gitlab.GitLabConfig(
                        null,
                        null,
                        null,
                        "https://gitlab.example/root/api/v4/"));

        assertThat(GitLabConfig.instanceBaseUrl(legacyConnection))
                .isEqualTo("https://gitlab.com");
        assertThat(GitLabConfig.instanceBaseUrl(selfManagedConnection))
                .isEqualTo("https://gitlab.example/root");
    }

    private static MockResponse jsonResponse(String body) {
        return new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(body);
    }
}

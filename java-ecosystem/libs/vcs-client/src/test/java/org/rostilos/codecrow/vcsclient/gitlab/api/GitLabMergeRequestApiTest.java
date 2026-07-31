package org.rostilos.codecrow.vcsclient.gitlab.api;

import okhttp3.OkHttpClient;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import org.junit.jupiter.api.Test;

import java.io.IOException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class GitLabMergeRequestApiTest {

    @Test
    void metadataAndCommentsUseOneConfiguredContext() throws Exception {
        try (MockWebServer gitLab = new MockWebServer()) {
            gitLab.enqueue(jsonResponse("""
                    {"iid": 17, "title": "Review"}
                    """));
            gitLab.enqueue(jsonResponse("{}"));
            gitLab.enqueue(jsonResponse("""
                    [{"id": 91, "body": "<!-- marker --> existing"}]
                    """));
            gitLab.start();

            GitLabMergeRequestApi mergeRequests =
                    new GitLabMergeRequestApi(new GitLabApiContext(
                            new OkHttpClient(),
                            gitLab.url("/gitlab").toString()));

            assertThat(mergeRequests.get("team", "repo", 17)
                    .path("title").asText()).isEqualTo("Review");
            mergeRequests.postComment("team", "repo", 17, "Review body");
            assertThat(mergeRequests.findNoteByMarker(
                    "team", "repo", 17, "<!-- marker -->")).isEqualTo(91L);

            assertThat(gitLab.takeRequest().getPath())
                    .isEqualTo("/gitlab/api/v4/projects/team%2Frepo"
                            + "/merge_requests/17");
            var commentRequest = gitLab.takeRequest();
            assertThat(commentRequest.getPath())
                    .isEqualTo("/gitlab/api/v4/projects/team%2Frepo"
                            + "/merge_requests/17/notes");
            assertThat(commentRequest.getBody().readUtf8())
                    .contains("Review body");
            assertThat(gitLab.takeRequest().getPath())
                    .isEqualTo("/gitlab/api/v4/projects/team%2Frepo"
                            + "/merge_requests/17/notes?per_page=100");
        }
    }

    @Test
    void requiredCommentFailureIsReported() throws Exception {
        try (MockWebServer gitLab = new MockWebServer()) {
            gitLab.enqueue(new MockResponse()
                    .setResponseCode(403)
                    .setBody("Forbidden"));
            gitLab.start();

            GitLabMergeRequestApi mergeRequests =
                    new GitLabMergeRequestApi(new GitLabApiContext(
                            new OkHttpClient(),
                            gitLab.url("/").toString()));

            assertThatThrownBy(() -> mergeRequests.postComment(
                    "team", "repo", 17, "Review"))
                    .isInstanceOf(IOException.class)
                    .hasMessageContaining("403")
                    .hasMessageContaining("Forbidden");
        }
    }

    private static MockResponse jsonResponse(String body) {
        return new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(body);
    }
}

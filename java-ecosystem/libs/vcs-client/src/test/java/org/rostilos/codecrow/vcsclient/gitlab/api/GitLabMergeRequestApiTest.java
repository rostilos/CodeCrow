package org.rostilos.codecrow.vcsclient.gitlab.api;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.OkHttpClient;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import org.junit.jupiter.api.Test;

import java.io.IOException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class GitLabMergeRequestApiTest {

    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

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

    @Test
    void lineCommentIncludesCompleteDiffPosition() throws Exception {
        try (MockWebServer gitLab = new MockWebServer()) {
            gitLab.enqueue(new MockResponse().setResponseCode(201).setBody("{}"));
            gitLab.start();

            GitLabMergeRequestApi mergeRequests =
                    new GitLabMergeRequestApi(new GitLabApiContext(
                            new OkHttpClient(),
                            gitLab.url("/").toString()));

            mergeRequests.postLineComment(
                    "team", "repo", 17, "Finding",
                    "base-sha", "head-sha", "start-sha",
                    "src/App.java", 42);

            var request = gitLab.takeRequest();
            JsonNode position = OBJECT_MAPPER.readTree(
                    request.getBody().readUtf8()).path("position");
            assertThat(request.getPath()).isEqualTo(
                    "/api/v4/projects/team%2Frepo/merge_requests/17/discussions");
            assertThat(position.path("base_sha").asText()).isEqualTo("base-sha");
            assertThat(position.path("head_sha").asText()).isEqualTo("head-sha");
            assertThat(position.path("start_sha").asText()).isEqualTo("start-sha");
            assertThat(position.path("position_type").asText()).isEqualTo("text");
            assertThat(position.path("old_path").asText()).isEqualTo("src/App.java");
            assertThat(position.path("new_path").asText()).isEqualTo("src/App.java");
            assertThat(position.path("new_line").asInt()).isEqualTo(42);
        }
    }

    @Test
    void lineCommentFailureIsReported() throws Exception {
        try (MockWebServer gitLab = new MockWebServer()) {
            gitLab.enqueue(new MockResponse()
                    .setResponseCode(422)
                    .setBody("Invalid diff position"));
            gitLab.start();

            GitLabMergeRequestApi mergeRequests =
                    new GitLabMergeRequestApi(new GitLabApiContext(
                            new OkHttpClient(),
                            gitLab.url("/").toString()));

            assertThatThrownBy(() -> mergeRequests.postLineComment(
                    "team", "repo", 17, "Finding",
                    "base-sha", "head-sha", "start-sha",
                    "src/App.java", 42))
                    .isInstanceOf(IOException.class)
                    .hasMessageContaining("422")
                    .hasMessageContaining("Invalid diff position");
        }
    }

    private static MockResponse jsonResponse(String body) {
        return new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(body);
    }
}

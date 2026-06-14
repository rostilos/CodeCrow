package org.rostilos.codecrow.taskmanagement.jira.cloud;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.taskmanagement.model.TaskCommentVisibility;
import org.rostilos.codecrow.taskmanagement.model.TaskCommentVisibilityOption;

import java.io.IOException;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("JiraCloudClient")
class JiraCloudClientTest {

    private final ObjectMapper mapper = new ObjectMapper();
    private MockWebServer server;
    private JiraCloudClient client;

    @BeforeEach
    void setUp() throws IOException {
        server = new MockWebServer();
        server.start();
        client = new JiraCloudClient(new JiraCloudConfig(
                server.url("").toString(),
                "qa@example.com",
                "token"
        ));
    }

    @AfterEach
    void tearDown() throws IOException {
        server.shutdown();
    }

    @Test
    @DisplayName("posts Jira comment with group visibility")
    void postCommentIncludesGroupVisibility() throws Exception {
        server.enqueue(commentResponse(201));

        client.postComment(
                "PROJ-123",
                "## QA\n\nTest checkout flow",
                new TaskCommentVisibility("group", "group-id-1", "qa-team")
        );

        RecordedRequest request = server.takeRequest();
        assertThat(request.getMethod()).isEqualTo("POST");
        assertThat(request.getPath()).isEqualTo("/rest/api/3/issue/PROJ-123/comment");

        JsonNode body = mapper.readTree(request.getBody().readUtf8());
        JsonNode visibility = body.path("visibility");
        assertThat(visibility.path("type").asText()).isEqualTo("group");
        assertThat(visibility.path("identifier").asText()).isEqualTo("group-id-1");
        assertThat(visibility.path("value").asText()).isEqualTo("qa-team");
    }

    @Test
    @DisplayName("updates Jira comment with group visibility")
    void updateCommentIncludesGroupVisibility() throws Exception {
        server.enqueue(commentResponse(200));

        client.updateComment(
                "PROJ-123",
                "10001",
                "Updated QA notes",
                new TaskCommentVisibility("group", "group-id-1", "qa-team")
        );

        RecordedRequest request = server.takeRequest();
        assertThat(request.getMethod()).isEqualTo("PUT");
        assertThat(request.getPath()).isEqualTo("/rest/api/3/issue/PROJ-123/comment/10001");

        JsonNode body = mapper.readTree(request.getBody().readUtf8());
        assertThat(body.path("visibility").path("identifier").asText()).isEqualTo("group-id-1");
    }

    @Test
    @DisplayName("lists Jira groups as comment visibility options")
    void listCommentVisibilityOptionsReturnsGroups() throws Exception {
        server.enqueue(jsonResponse("""
                {
                  "isLast": false,
                  "maxResults": 2,
                  "startAt": 0,
                  "total": 3,
                  "values": [
                    {"groupId": "gid-1", "name": "qa-team"},
                    {"groupId": "gid-2", "name": "developers"}
                  ]
                }
                """));
        server.enqueue(jsonResponse("""
                {
                  "isLast": true,
                  "maxResults": 2,
                  "startAt": 2,
                  "total": 3,
                  "values": [
                    {"groupId": "gid-3", "name": "release-managers"}
                  ]
                }
                """));

        List<TaskCommentVisibilityOption> options = client.listCommentVisibilityOptions();

        assertThat(options).containsExactly(
                new TaskCommentVisibilityOption("group", "gid-1", "qa-team", "qa-team"),
                new TaskCommentVisibilityOption("group", "gid-2", "developers", "developers"),
                new TaskCommentVisibilityOption("group", "gid-3", "release-managers", "release-managers")
        );

        RecordedRequest first = server.takeRequest();
        RecordedRequest second = server.takeRequest();
        assertThat(first.getPath()).contains("/rest/api/3/group/bulk");
        assertThat(first.getPath()).contains("startAt=0");
        assertThat(second.getPath()).contains("startAt=2");
    }

    private MockResponse commentResponse(int status) {
        return jsonResponse("""
                {
                  "id": "10001",
                  "author": {"displayName": "CodeCrow"},
                  "body": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                      {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "QA notes"}]
                      }
                    ]
                  }
                }
                """).setResponseCode(status);
    }

    private MockResponse jsonResponse(String body) {
        return new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(body);
    }
}

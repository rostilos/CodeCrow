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
        assertThat(visibility.has("value")).isFalse();
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
        assertThat(body.path("visibility").has("value")).isFalse();
    }

    @Test
    @DisplayName("posts Jira comment with project role visibility")
    void postCommentIncludesProjectRoleVisibility() throws Exception {
        server.enqueue(commentResponse(201));

        client.postComment(
                "PROJ-123",
                "## QA\n\nTest checkout flow",
                new TaskCommentVisibility("role", "Developers", "Developers")
        );

        RecordedRequest request = server.takeRequest();
        assertThat(request.getMethod()).isEqualTo("POST");
        assertThat(request.getPath()).isEqualTo("/rest/api/3/issue/PROJ-123/comment");

        JsonNode body = mapper.readTree(request.getBody().readUtf8());
        JsonNode visibility = body.path("visibility");
        assertThat(visibility.path("type").asText()).isEqualTo("role");
        assertThat(visibility.path("identifier").asText()).isEqualTo("Developers");
        assertThat(visibility.path("value").asText()).isEqualTo("Developers");
    }

    @Test
    @DisplayName("lists Jira groups and project roles as comment visibility options")
    void listCommentVisibilityOptionsReturnsGroupsAndRoles() throws Exception {
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
        server.enqueue(jsonResponse("""
                [
                  {"id": 10000, "name": "Developers"},
                  {"id": 10001, "name": "Perspective", "translatedName": "Perspective"}
                ]
                """));

        List<TaskCommentVisibilityOption> options = client.listCommentVisibilityOptions();

        assertThat(options).containsExactly(
                new TaskCommentVisibilityOption("group", "gid-1", "qa-team", "qa-team"),
                new TaskCommentVisibilityOption("group", "gid-2", "developers", "developers"),
                new TaskCommentVisibilityOption("group", "gid-3", "release-managers", "release-managers"),
                new TaskCommentVisibilityOption("role", "Developers", "Developers", "Developers"),
                new TaskCommentVisibilityOption("role", "Perspective", "Perspective", "Perspective")
        );

        RecordedRequest first = server.takeRequest();
        RecordedRequest second = server.takeRequest();
        RecordedRequest third = server.takeRequest();
        assertThat(first.getPath()).contains("/rest/api/3/group/bulk");
        assertThat(first.getPath()).contains("startAt=0");
        assertThat(second.getPath()).contains("startAt=2");
        assertThat(third.getPath()).isEqualTo("/rest/api/3/role");
    }

    @Test
    @DisplayName("returns Jira groups when project roles cannot be listed")
    void listCommentVisibilityOptionsReturnsGroupsWhenRolesUnavailable() throws Exception {
        server.enqueue(jsonResponse("""
                {
                  "isLast": true,
                  "maxResults": 100,
                  "startAt": 0,
                  "total": 1,
                  "values": [
                    {"groupId": "gid-1", "name": "qa-team"}
                  ]
                }
                """));
        server.enqueue(jsonResponse("""
                {"errorMessages":["Forbidden"]}
                """).setResponseCode(403));

        List<TaskCommentVisibilityOption> options = client.listCommentVisibilityOptions();

        assertThat(options).containsExactly(
                new TaskCommentVisibilityOption("group", "gid-1", "qa-team", "qa-team")
        );
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

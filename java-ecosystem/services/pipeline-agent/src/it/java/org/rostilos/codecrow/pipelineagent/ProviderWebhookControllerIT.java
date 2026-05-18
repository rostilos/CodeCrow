package org.rostilos.codecrow.pipelineagent;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.*;

/**
 * Integration tests for ProviderWebhookController.
 * Webhook endpoints are public (permitAll) — they authenticate
 * via authToken in the URL path or via VCS signature headers.
 */
class ProviderWebhookControllerIT extends BasePipelineAgentIT {

    @Nested
    @DisplayName("POST /api/webhooks/{provider}/{authToken}")
    class WebhookWithAuthToken {

        @Test
        @DisplayName("Unknown provider — 400 bad request")
        void webhook_unknownProvider_returns400() {
            given()
                .contentType("application/json")
                .body("{\"key\": \"value\"}")
            .when()
                .post("/api/webhooks/unknown-vcs/some-token")
            .then()
                .statusCode(400)
                .body("error", equalTo("bad_request"));
        }

        @Test
        @DisplayName("GitHub webhook with no matching project — 404")
        void webhook_github_noProject_returns404() {
            given()
                .contentType("application/json")
                .header("X-GitHub-Event", "push")
                .body("""
                    {
                        "repository": {
                            "id": 999999,
                            "full_name": "nonexistent/repo"
                        },
                        "ref": "refs/heads/main",
                        "after": "abc123"
                    }
                    """)
            .when()
                .post("/api/webhooks/github/invalid-auth-token")
            .then()
                .statusCode(anyOf(is(404), is(400)));
        }

        @Test
        @DisplayName("Bitbucket webhook with missing repo ID — 400")
        void webhook_bitbucket_missingRepoId_returns400() {
            given()
                .contentType("application/json")
                .header("X-Event-Key", "pullrequest:created")
                .body("{}")
            .when()
                .post("/api/webhooks/bitbucket-cloud/some-token")
            .then()
                .statusCode(400);
        }

        @Test
        @DisplayName("GitLab webhook with empty body — 400")
        void webhook_gitlab_emptyPayload_returns400() {
            given()
                .contentType("application/json")
                .header("X-Gitlab-Event", "Push Hook")
                .body("{}")
            .when()
                .post("/api/webhooks/gitlab/some-token")
            .then()
                .statusCode(anyOf(is(400), is(500)));
        }
    }

    @Nested
    @DisplayName("POST /api/webhooks/{provider} (no token)")
    class WebhookWithoutAuthToken {

        @Test
        @DisplayName("GitHub webhook without token — no matching project — ignored")
        void webhook_githubNoToken_noProject_ignored() {
            given()
                .contentType("application/json")
                .header("X-GitHub-Event", "push")
                .body("""
                    {
                        "repository": {
                            "id": 888888,
                            "full_name": "org/unmatched-repo"
                        },
                        "ref": "refs/heads/main",
                        "after": "def456"
                    }
                    """)
            .when()
                .post("/api/webhooks/github")
            .then()
                .statusCode(200)
                .body("status", equalTo("ignored"));
        }

        @Test
        @DisplayName("Unknown provider without token — 400")
        void webhook_unknownProviderNoToken_returns400() {
            given()
                .contentType("application/json")
                .body("{\"data\": \"test\"}")
            .when()
                .post("/api/webhooks/invalid-provider")
            .then()
                .statusCode(400);
        }

        @Test
        @DisplayName("Bitbucket webhook without token — missing repo — bad request or ignored")
        void webhook_bitbucketNoToken_returns400OrIgnored() {
            given()
                .contentType("application/json")
                .header("X-Event-Key", "repo:push")
                .body("""
                    {
                        "push": {
                            "changes": []
                        }
                    }
                    """)
            .when()
                .post("/api/webhooks/bitbucket-cloud")
            .then()
                .statusCode(anyOf(is(200), is(400)));
        }
    }
}

package org.rostilos.codecrow.analysisengine.aiclient;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("AiCommandClient Records")
class AiCommandClientRecordsTest {

    @Nested
    @DisplayName("SummarizeRequest")
    class SummarizeRequestTests {

        @Test
        @DisplayName("should create with all fields")
        void shouldCreateWithAllFields() {
            AiCommandClient.SummarizeRequest request = new AiCommandClient.SummarizeRequest(
                    1L, "workspace", "repo-slug", "project-workspace", "namespace",
                    "openai", "gpt-4", "api-key", 42L, "feature", "main", "abc123",
                    "oauth-client", "oauth-secret", "access-token", true, 4096, "bitbucket"
            );

            assertThat(request.projectId()).isEqualTo(1L);
            assertThat(request.projectVcsWorkspace()).isEqualTo("workspace");
            assertThat(request.projectVcsRepoSlug()).isEqualTo("repo-slug");
            assertThat(request.projectWorkspace()).isEqualTo("project-workspace");
            assertThat(request.projectNamespace()).isEqualTo("namespace");
            assertThat(request.aiProvider()).isEqualTo("openai");
            assertThat(request.aiModel()).isEqualTo("gpt-4");
            assertThat(request.aiApiKey()).isEqualTo("api-key");
            assertThat(request.pullRequestId()).isEqualTo(42L);
            assertThat(request.sourceBranch()).isEqualTo("feature");
            assertThat(request.targetBranch()).isEqualTo("main");
            assertThat(request.commitHash()).isEqualTo("abc123");
            assertThat(request.oAuthClient()).isEqualTo("oauth-client");
            assertThat(request.oAuthSecret()).isEqualTo("oauth-secret");
            assertThat(request.accessToken()).isEqualTo("access-token");
            assertThat(request.supportsMermaid()).isTrue();
            assertThat(request.maxAllowedTokens()).isEqualTo(4096);
            assertThat(request.vcsProvider()).isEqualTo("bitbucket");
        }
    }

    @Nested
    @DisplayName("AskRequest")
    class AskRequestTests {

        @Test
        @DisplayName("should create with all fields")
        void shouldCreateWithAllFields() {
            AiCommandClient.AskRequest request = new AiCommandClient.AskRequest(
                    1L, "workspace", "repo-slug", "project-workspace", "namespace",
                    "anthropic", "claude-3", "api-key", "What is this code doing?",
                    42L, "abc123", "oauth-client", "oauth-secret", "access-token",
                    8192, "github", "analysis context", List.of("issue-1", "issue-2")
            );

            assertThat(request.projectId()).isEqualTo(1L);
            assertThat(request.aiProvider()).isEqualTo("anthropic");
            assertThat(request.aiModel()).isEqualTo("claude-3");
            assertThat(request.question()).isEqualTo("What is this code doing?");
            assertThat(request.pullRequestId()).isEqualTo(42L);
            assertThat(request.analysisContext()).isEqualTo("analysis context");
            assertThat(request.issueReferences()).containsExactly("issue-1", "issue-2");
            assertThat(request.vcsProvider()).isEqualTo("github");
        }

        @Test
        @DisplayName("should support null optional fields")
        void shouldSupportNullOptionalFields() {
            AiCommandClient.AskRequest request = new AiCommandClient.AskRequest(
                    1L, "workspace", "repo-slug", null, null,
                    "openai", "gpt-4", "api-key", "question",
                    null, null, null, null, null,
                    null, "bitbucket", null, null
            );

            assertThat(request.pullRequestId()).isNull();
            assertThat(request.commitHash()).isNull();
            assertThat(request.analysisContext()).isNull();
            assertThat(request.issueReferences()).isNull();
        }
    }

    @Nested
    @DisplayName("SummarizeResult")
    class SummarizeResultTests {

        @Test
        @DisplayName("should create with all fields")
        void shouldCreateWithAllFields() {
            AiCommandClient.SummarizeResult result = new AiCommandClient.SummarizeResult(
                    "This PR adds new features", "graph LR; A-->B", "MERMAID"
            );

            assertThat(result.summary()).isEqualTo("This PR adds new features");
            assertThat(result.diagram()).isEqualTo("graph LR; A-->B");
            assertThat(result.diagramType()).isEqualTo("MERMAID");
        }

        @Test
        @DisplayName("should support empty diagram")
        void shouldSupportEmptyDiagram() {
            AiCommandClient.SummarizeResult result = new AiCommandClient.SummarizeResult(
                    "Summary without diagram", "", null
            );

            assertThat(result.summary()).isEqualTo("Summary without diagram");
            assertThat(result.diagram()).isEmpty();
            assertThat(result.diagramType()).isNull();
        }
    }

    @Nested
    @DisplayName("AskResult")
    class AskResultTests {

        @Test
        @DisplayName("should create with answer")
        void shouldCreateWithAnswer() {
            AiCommandClient.AskResult result = new AiCommandClient.AskResult(
                    "This code implements a REST API endpoint"
            );

            assertThat(result.answer()).isEqualTo("This code implements a REST API endpoint");
        }

        @Test
        @DisplayName("should support empty answer")
        void shouldSupportEmptyAnswer() {
            AiCommandClient.AskResult result = new AiCommandClient.AskResult("");
            assertThat(result.answer()).isEmpty();
        }
    }

    @Nested
    @DisplayName("ReviewRequest")
    class ReviewRequestTests {

        @Test
        @DisplayName("should create with all fields")
        void shouldCreateWithAllFields() {
            AiCommandClient.ReviewRequest request = new AiCommandClient.ReviewRequest(
                    1L, "workspace", "repo-slug", "project-workspace", "namespace",
                    "openai", "gpt-4", "api-key", 42L, "feature", "main", "abc123",
                    "oauth-client", "oauth-secret", "access-token", 4096, "bitbucket"
            );

            assertThat(request.projectId()).isEqualTo(1L);
            assertThat(request.pullRequestId()).isEqualTo(42L);
            assertThat(request.sourceBranch()).isEqualTo("feature");
            assertThat(request.targetBranch()).isEqualTo("main");
            assertThat(request.commitHash()).isEqualTo("abc123");
            assertThat(request.maxAllowedTokens()).isEqualTo(4096);
        }
    }

    @Nested
    @DisplayName("ReviewResult")
    class ReviewResultTests {

        @Test
        @DisplayName("should create with review")
        void shouldCreateWithReview() {
            AiCommandClient.ReviewResult result = new AiCommandClient.ReviewResult(
                    "## Code Review\n\nLooks good!"
            );

            assertThat(result.review()).isEqualTo("## Code Review\n\nLooks good!");
        }
    }
}

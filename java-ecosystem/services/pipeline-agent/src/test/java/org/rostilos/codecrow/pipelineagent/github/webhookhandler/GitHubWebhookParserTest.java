package org.rostilos.codecrow.pipelineagent.github.webhookhandler;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("GitHubWebhookParser")
class GitHubWebhookParserTest {

    private GitHubWebhookParser parser;
    private ObjectMapper objectMapper;

    @BeforeEach
    void setUp() {
        parser = new GitHubWebhookParser();
        objectMapper = new ObjectMapper();
    }

    @Nested
    @DisplayName("parse() - Pull Request Events")
    class ParsePullRequestEvents {

        @Test
        @DisplayName("should parse PR opened event")
        void shouldParsePrOpenedEvent() throws Exception {
            String payload = """
                    {
                        "repository": {
                            "id": 123456789,
                            "name": "my-repo",
                            "owner": {
                                "login": "my-org"
                            }
                        },
                        "pull_request": {
                            "number": 42,
                            "head": {
                                "ref": "feature-branch",
                                "sha": "abc123def456"
                            },
                            "base": {
                                "ref": "main"
                            },
                            "user": {
                                "id": 987654,
                                "login": "johndoe"
                            }
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("pull_request", jsonNode);

            assertThat(result.provider()).isEqualTo(EVcsProvider.GITHUB);
            assertThat(result.eventType()).isEqualTo("pull_request");
            assertThat(result.externalRepoId()).isEqualTo("123456789");
            assertThat(result.repoSlug()).isEqualTo("my-repo");
            assertThat(result.workspaceSlug()).isEqualTo("my-org");
            assertThat(result.pullRequestId()).isEqualTo("42");
            assertThat(result.sourceBranch()).isEqualTo("feature-branch");
            assertThat(result.targetBranch()).isEqualTo("main");
            assertThat(result.commitHash()).isEqualTo("abc123def456");
            assertThat(result.prAuthorId()).isEqualTo("987654");
            assertThat(result.prAuthorUsername()).isEqualTo("johndoe");
        }

        @Test
        @DisplayName("should parse PR synchronize event")
        void shouldParsePrSynchronizeEvent() throws Exception {
            String payload = """
                    {
                        "repository": {
                            "id": 111111,
                            "name": "test-repo",
                            "owner": {"login": "test-owner"}
                        },
                        "pull_request": {
                            "number": 99,
                            "head": {
                                "ref": "update-branch",
                                "sha": "newcommithash"
                            },
                            "base": {"ref": "develop"}
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("pull_request", jsonNode);

            assertThat(result.pullRequestId()).isEqualTo("99");
            assertThat(result.commitHash()).isEqualTo("newcommithash");
        }
    }

    @Nested
    @DisplayName("parse() - Push Events")
    class ParsePushEvents {

        @Test
        @DisplayName("should parse push event")
        void shouldParsePushEvent() throws Exception {
            String payload = """
                    {
                        "repository": {
                            "id": 222222,
                            "name": "push-repo",
                            "owner": {"login": "push-owner"}
                        },
                        "ref": "refs/heads/main",
                        "after": "pushcommithash123"
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("push", jsonNode);

            assertThat(result.eventType()).isEqualTo("push");
            assertThat(result.sourceBranch()).isEqualTo("main");
            assertThat(result.commitHash()).isEqualTo("pushcommithash123");
        }

        @Test
        @DisplayName("should handle feature branch push")
        void shouldHandleFeatureBranchPush() throws Exception {
            String payload = """
                    {
                        "repository": {
                            "id": 333333,
                            "name": "repo",
                            "owner": {"login": "owner"}
                        },
                        "ref": "refs/heads/feature/my-feature",
                        "after": "featurehash"
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("push", jsonNode);

            assertThat(result.sourceBranch()).isEqualTo("feature/my-feature");
        }

        @Test
        @DisplayName("should handle branch deletion (skip null commit)")
        void shouldHandleBranchDeletion() throws Exception {
            String payload = """
                    {
                        "repository": {
                            "id": 444444,
                            "name": "repo",
                            "owner": {"login": "owner"}
                        },
                        "ref": "refs/heads/deleted-branch",
                        "after": "0000000000000000000000000000000000000000"
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("push", jsonNode);

            assertThat(result.commitHash()).isNull();
        }
    }

    @Nested
    @DisplayName("parse() - Issue Comment Events")
    class ParseIssueCommentEvents {

        @Test
        @DisplayName("should parse issue comment on PR")
        void shouldParseIssueCommentOnPr() throws Exception {
            String payload = """
                    {
                        "repository": {
                            "id": 555555,
                            "name": "repo",
                            "owner": {"login": "owner"}
                        },
                        "issue": {
                            "number": 50,
                            "pull_request": {},
                            "user": {
                                "id": 111,
                                "login": "pr-author"
                            }
                        },
                        "comment": {
                            "id": 12345678,
                            "body": "This is a comment",
                            "user": {
                                "id": 222,
                                "login": "commenter"
                            }
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("issue_comment", jsonNode);

            assertThat(result.pullRequestId()).isEqualTo("50");
            assertThat(result.prAuthorId()).isEqualTo("111");
            assertThat(result.prAuthorUsername()).isEqualTo("pr-author");
            assertThat(result.commentData()).isNotNull();
            assertThat(result.commentData().commentId()).isEqualTo("12345678");
            assertThat(result.commentData().commentBody()).isEqualTo("This is a comment");
            assertThat(result.commentData().commentAuthorId()).isEqualTo("222");
            assertThat(result.commentData().commentAuthorUsername()).isEqualTo("commenter");
            assertThat(result.commentData().isInlineComment()).isFalse();
        }
    }

    @Nested
    @DisplayName("parse() - PR Review Comment Events")
    class ParsePrReviewCommentEvents {

        @Test
        @DisplayName("should parse PR review comment (inline)")
        void shouldParsePrReviewComment() throws Exception {
            String payload = """
                    {
                        "repository": {
                            "id": 666666,
                            "name": "repo",
                            "owner": {"login": "owner"}
                        },
                        "pull_request": {
                            "number": 75,
                            "head": {"ref": "feature", "sha": "hash123"},
                            "base": {"ref": "main"},
                            "user": {"id": 333, "login": "author"}
                        },
                        "comment": {
                            "id": 99999999,
                            "body": "Consider using a constant here",
                            "user": {"id": 444, "login": "reviewer"},
                            "path": "src/main/java/App.java",
                            "line": 42
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("pull_request_review_comment", jsonNode);

            assertThat(result.commentData()).isNotNull();
            assertThat(result.commentData().isInlineComment()).isTrue();
            assertThat(result.commentData().filePath()).isEqualTo("src/main/java/App.java");
            assertThat(result.commentData().lineNumber()).isEqualTo(42);
        }

        @Test
        @DisplayName("should parse reply to PR review comment")
        void shouldParseReplyToReviewComment() throws Exception {
            String payload = """
                    {
                        "repository": {
                            "id": 777777,
                            "name": "repo",
                            "owner": {"login": "owner"}
                        },
                        "pull_request": {
                            "number": 80,
                            "head": {"ref": "feature", "sha": "hash"},
                            "base": {"ref": "main"}
                        },
                        "comment": {
                            "id": 88888888,
                            "body": "Good point, will fix",
                            "user": {"id": 555, "login": "author"},
                            "in_reply_to_id": 77777777,
                            "path": "src/App.java",
                            "original_line": 25
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("pull_request_review_comment", jsonNode);

            assertThat(result.commentData().parentCommentId()).isEqualTo("77777777");
            assertThat(result.commentData().lineNumber()).isEqualTo(25);
        }
    }

    @Nested
    @DisplayName("parse() - Edge Cases")
    class ParseEdgeCases {

        @Test
        @DisplayName("should handle empty payload")
        void shouldHandleEmptyPayload() throws Exception {
            JsonNode jsonNode = objectMapper.readTree("{}");
            WebhookPayload result = parser.parse("unknown", jsonNode);

            assertThat(result.provider()).isEqualTo(EVcsProvider.GITHUB);
            assertThat(result.externalRepoId()).isNull();
            assertThat(result.repoSlug()).isNull();
        }

        @Test
        @DisplayName("should handle missing owner")
        void shouldHandleMissingOwner() throws Exception {
            String payload = """
                    {
                        "repository": {
                            "id": 999999,
                            "name": "orphan-repo"
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("push", jsonNode);

            assertThat(result.repoSlug()).isEqualTo("orphan-repo");
            assertThat(result.workspaceSlug()).isNull();
        }

        @Test
        @DisplayName("should handle null event type")
        void shouldHandleNullEventType() throws Exception {
            String payload = """
                    {
                        "repository": {
                            "id": 111,
                            "name": "repo",
                            "owner": {"login": "owner"}
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse(null, jsonNode);

            assertThat(result.eventType()).isNull();
        }
    }
}

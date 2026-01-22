package org.rostilos.codecrow.pipelineagent.gitlab.webhookhandler;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("GitLabWebhookParser")
class GitLabWebhookParserTest {

    private GitLabWebhookParser parser;
    private ObjectMapper objectMapper;

    @BeforeEach
    void setUp() {
        parser = new GitLabWebhookParser();
        objectMapper = new ObjectMapper();
    }

    @Nested
    @DisplayName("parse() - Merge Request Events")
    class ParseMergeRequestEvents {

        @Test
        @DisplayName("should parse MR opened event")
        void shouldParseMrOpenedEvent() throws Exception {
            String payload = """
                    {
                        "project": {
                            "id": 123456,
                            "path": "my-repo",
                            "path_with_namespace": "my-group/my-repo"
                        },
                        "object_attributes": {
                            "iid": 42,
                            "source_branch": "feature-branch",
                            "target_branch": "main",
                            "last_commit": {
                                "id": "abc123def456",
                                "author": {
                                    "name": "John Doe"
                                }
                            }
                        },
                        "user": {
                            "id": 987654,
                            "username": "johndoe"
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("Merge Request Hook", jsonNode);

            assertThat(result.provider()).isEqualTo(EVcsProvider.GITLAB);
            assertThat(result.eventType()).isEqualTo("merge_request");
            assertThat(result.externalRepoId()).isEqualTo("123456");
            assertThat(result.repoSlug()).isEqualTo("my-repo");
            assertThat(result.workspaceSlug()).isEqualTo("my-group");
            assertThat(result.pullRequestId()).isEqualTo("42");
            assertThat(result.sourceBranch()).isEqualTo("feature-branch");
            assertThat(result.targetBranch()).isEqualTo("main");
            assertThat(result.commitHash()).isEqualTo("abc123def456");
            assertThat(result.prAuthorId()).isEqualTo("987654");
            assertThat(result.prAuthorUsername()).isEqualTo("John Doe");
        }

        @Test
        @DisplayName("should parse MR update event")
        void shouldParseMrUpdateEvent() throws Exception {
            String payload = """
                    {
                        "project": {
                            "id": 111111,
                            "path": "test-repo",
                            "path_with_namespace": "test-group/subgroup/test-repo"
                        },
                        "object_attributes": {
                            "iid": 99,
                            "source_branch": "update-branch",
                            "target_branch": "develop",
                            "last_commit": {"id": "newhash123"}
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("merge_request", jsonNode);

            assertThat(result.pullRequestId()).isEqualTo("99");
            assertThat(result.workspaceSlug()).isEqualTo("test-group/subgroup");
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
                        "project": {
                            "id": 222222,
                            "path": "push-repo",
                            "path_with_namespace": "push-group/push-repo"
                        },
                        "ref": "refs/heads/main",
                        "after": "pushcommithash123"
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("Push Hook", jsonNode);

            assertThat(result.eventType()).isEqualTo("push");
            assertThat(result.sourceBranch()).isEqualTo("main");
            assertThat(result.commitHash()).isEqualTo("pushcommithash123");
        }

        @Test
        @DisplayName("should handle feature branch push")
        void shouldHandleFeatureBranchPush() throws Exception {
            String payload = """
                    {
                        "project": {
                            "id": 333333,
                            "path": "repo",
                            "path_with_namespace": "group/repo"
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
        @DisplayName("should handle branch deletion")
        void shouldHandleBranchDeletion() throws Exception {
            String payload = """
                    {
                        "project": {
                            "id": 444444,
                            "path": "repo",
                            "path_with_namespace": "group/repo"
                        },
                        "ref": "refs/heads/deleted-branch",
                        "after": "0000000000000000000000000000000000000000"
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("Push Hook", jsonNode);

            assertThat(result.commitHash()).isNull();
        }
    }

    @Nested
    @DisplayName("parse() - Note (Comment) Events")
    class ParseNoteEvents {

        @Test
        @DisplayName("should parse MR comment")
        void shouldParseMrComment() throws Exception {
            String payload = """
                    {
                        "project": {
                            "id": 555555,
                            "path": "repo",
                            "path_with_namespace": "group/repo"
                        },
                        "object_attributes": {
                            "id": 12345678,
                            "note": "This is a comment on the MR",
                            "noteable_type": "MergeRequest"
                        },
                        "user": {
                            "id": 111,
                            "username": "commenter"
                        },
                        "merge_request": {
                            "iid": 50,
                            "source_branch": "feature",
                            "target_branch": "main",
                            "last_commit": {"id": "mrcommithash"},
                            "author_id": 222
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("Note Hook", jsonNode);

            assertThat(result.eventType()).isEqualTo("note");
            assertThat(result.pullRequestId()).isEqualTo("50");
            assertThat(result.commentData()).isNotNull();
            assertThat(result.commentData().commentId()).isEqualTo("12345678");
            assertThat(result.commentData().commentBody()).isEqualTo("This is a comment on the MR");
            assertThat(result.commentData().commentAuthorId()).isEqualTo("111");
            assertThat(result.commentData().commentAuthorUsername()).isEqualTo("commenter");
            assertThat(result.commentData().isInlineComment()).isFalse();
        }

        @Test
        @DisplayName("should parse inline comment on MR")
        void shouldParseInlineComment() throws Exception {
            String payload = """
                    {
                        "project": {
                            "id": 666666,
                            "path": "repo",
                            "path_with_namespace": "group/repo"
                        },
                        "object_attributes": {
                            "id": 99999999,
                            "note": "Consider refactoring this",
                            "noteable_type": "MergeRequest",
                            "position": {
                                "new_path": "src/main/java/App.java",
                                "new_line": 42
                            }
                        },
                        "user": {"id": 333, "username": "reviewer"},
                        "merge_request": {
                            "iid": 75,
                            "source_branch": "feature",
                            "target_branch": "main",
                            "last_commit": {"id": "hash123"}
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("note", jsonNode);

            assertThat(result.commentData().isInlineComment()).isTrue();
            assertThat(result.commentData().filePath()).isEqualTo("src/main/java/App.java");
            assertThat(result.commentData().lineNumber()).isEqualTo(42);
        }

        @Test
        @DisplayName("should parse inline comment with old_path fallback")
        void shouldParseInlineCommentWithOldPath() throws Exception {
            String payload = """
                    {
                        "project": {
                            "id": 777777,
                            "path": "repo",
                            "path_with_namespace": "group/repo"
                        },
                        "object_attributes": {
                            "id": 88888888,
                            "note": "This line was removed",
                            "noteable_type": "MergeRequest",
                            "position": {
                                "old_path": "src/OldFile.java",
                                "old_line": 15
                            }
                        },
                        "user": {"id": 444, "username": "reviewer"},
                        "merge_request": {
                            "iid": 80,
                            "source_branch": "refactor",
                            "target_branch": "main",
                            "last_commit": {"id": "hash456"}
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("Note Hook", jsonNode);

            assertThat(result.commentData().filePath()).isEqualTo("src/OldFile.java");
            assertThat(result.commentData().lineNumber()).isEqualTo(15);
        }

        @Test
        @DisplayName("should skip non-MR comments")
        void shouldSkipNonMrComments() throws Exception {
            String payload = """
                    {
                        "project": {
                            "id": 888888,
                            "path": "repo",
                            "path_with_namespace": "group/repo"
                        },
                        "object_attributes": {
                            "id": 11111111,
                            "note": "Comment on issue",
                            "noteable_type": "Issue"
                        },
                        "user": {"id": 555, "username": "user"}
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("Note Hook", jsonNode);

            assertThat(result.commentData()).isNull();
        }
    }

    @Nested
    @DisplayName("normalizeEventType()")
    class NormalizeEventTypeTests {

        @Test
        @DisplayName("should normalize Merge Request Hook")
        void shouldNormalizeMergeRequestHook() throws Exception {
            String payload = """
                    {
                        "project": {"id": 1, "path": "r", "path_with_namespace": "g/r"},
                        "object_attributes": {"iid": 1, "source_branch": "a", "target_branch": "b"}
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("Merge Request Hook", jsonNode);

            assertThat(result.eventType()).isEqualTo("merge_request");
        }

        @Test
        @DisplayName("should normalize Push Hook")
        void shouldNormalizePushHook() throws Exception {
            String payload = """
                    {
                        "project": {"id": 1, "path": "r", "path_with_namespace": "g/r"},
                        "ref": "refs/heads/main"
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("Push Hook", jsonNode);

            assertThat(result.eventType()).isEqualTo("push");
        }

        @Test
        @DisplayName("should normalize Note Hook")
        void shouldNormalizeNoteHook() throws Exception {
            String payload = """
                    {
                        "project": {"id": 1, "path": "r", "path_with_namespace": "g/r"},
                        "object_attributes": {"id": 1, "note": "test", "noteable_type": "MergeRequest"},
                        "user": {"id": 1, "username": "u"},
                        "merge_request": {"iid": 1, "source_branch": "a", "target_branch": "b", "last_commit": {"id": "h"}}
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("Note Hook", jsonNode);

            assertThat(result.eventType()).isEqualTo("note");
        }

        @Test
        @DisplayName("should handle null event type")
        void shouldHandleNullEventType() throws Exception {
            String payload = """
                    {
                        "project": {"id": 1, "path": "r", "path_with_namespace": "g/r"}
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse(null, jsonNode);

            assertThat(result.eventType()).isNull();
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

            assertThat(result.provider()).isEqualTo(EVcsProvider.GITLAB);
            assertThat(result.externalRepoId()).isNull();
        }

        @Test
        @DisplayName("should handle project without namespace separator")
        void shouldHandleProjectWithoutNamespaceSeparator() throws Exception {
            String payload = """
                    {
                        "project": {
                            "id": 999999,
                            "path": "single-repo",
                            "path_with_namespace": "single-repo"
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("push", jsonNode);

            assertThat(result.repoSlug()).isEqualTo("single-repo");
            assertThat(result.workspaceSlug()).isNull();
        }
    }
}

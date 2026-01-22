package org.rostilos.codecrow.pipelineagent.bitbucket.webhookhandler;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("BitbucketCloudWebhookParser")
class BitbucketCloudWebhookParserTest {

    private BitbucketCloudWebhookParser parser;
    private ObjectMapper objectMapper;

    @BeforeEach
    void setUp() {
        parser = new BitbucketCloudWebhookParser();
        objectMapper = new ObjectMapper();
    }

    @Nested
    @DisplayName("parse() - Pull Request Events")
    class ParsePullRequestEvents {

        @Test
        @DisplayName("should parse PR created event")
        void shouldParsePrCreatedEvent() throws Exception {
            String payload = """
                    {
                        "repository": {
                            "uuid": "{abc-123}",
                            "name": "my-repo",
                            "workspace": {
                                "slug": "my-workspace"
                            }
                        },
                        "pullrequest": {
                            "id": 42,
                            "source": {
                                "branch": {"name": "feature-branch"},
                                "commit": {"hash": "abc123def"}
                            },
                            "destination": {
                                "branch": {"name": "main"}
                            },
                            "author": {
                                "uuid": "{user-456}",
                                "username": "johndoe"
                            }
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("pullrequest:created", jsonNode);

            assertThat(result.provider()).isEqualTo(EVcsProvider.BITBUCKET_CLOUD);
            assertThat(result.eventType()).isEqualTo("pullrequest:created");
            assertThat(result.externalRepoId()).isEqualTo("abc-123");
            assertThat(result.repoSlug()).isEqualTo("my-repo");
            assertThat(result.workspaceSlug()).isEqualTo("my-workspace");
            assertThat(result.pullRequestId()).isEqualTo("42");
            assertThat(result.sourceBranch()).isEqualTo("feature-branch");
            assertThat(result.targetBranch()).isEqualTo("main");
            assertThat(result.commitHash()).isEqualTo("abc123def");
            assertThat(result.prAuthorId()).isEqualTo("user-456");
            assertThat(result.prAuthorUsername()).isEqualTo("johndoe");
        }

        @Test
        @DisplayName("should parse PR updated event")
        void shouldParsePrUpdatedEvent() throws Exception {
            String payload = """
                    {
                        "repository": {
                            "uuid": "{repo-uuid}",
                            "name": "test-repo",
                            "workspace": {"slug": "test-workspace"}
                        },
                        "pullrequest": {
                            "id": 99,
                            "source": {
                                "branch": {"name": "update-branch"},
                                "commit": {"hash": "updated-hash"}
                            },
                            "destination": {"branch": {"name": "develop"}}
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("pullrequest:updated", jsonNode);

            assertThat(result.pullRequestId()).isEqualTo("99");
            assertThat(result.sourceBranch()).isEqualTo("update-branch");
            assertThat(result.targetBranch()).isEqualTo("develop");
            assertThat(result.commitHash()).isEqualTo("updated-hash");
        }

        @Test
        @DisplayName("should use owner username when workspace is missing")
        void shouldUseOwnerUsernameWhenWorkspaceMissing() throws Exception {
            String payload = """
                    {
                        "repository": {
                            "uuid": "{repo-uuid}",
                            "name": "personal-repo",
                            "owner": {
                                "username": "personal-user"
                            }
                        },
                        "pullrequest": {
                            "id": 1,
                            "source": {"branch": {"name": "branch"}, "commit": {"hash": "hash"}},
                            "destination": {"branch": {"name": "main"}}
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("pullrequest:created", jsonNode);

            assertThat(result.workspaceSlug()).isEqualTo("personal-user");
        }

        @Test
        @DisplayName("should use nickname when username is missing for author")
        void shouldUseNicknameWhenUsernameMissing() throws Exception {
            String payload = """
                    {
                        "repository": {
                            "uuid": "{repo-uuid}",
                            "name": "repo",
                            "workspace": {"slug": "ws"}
                        },
                        "pullrequest": {
                            "id": 1,
                            "source": {"branch": {"name": "branch"}, "commit": {"hash": "hash"}},
                            "destination": {"branch": {"name": "main"}},
                            "author": {
                                "uuid": "{author-uuid}",
                                "nickname": "author-nickname"
                            }
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("pullrequest:created", jsonNode);

            assertThat(result.prAuthorUsername()).isEqualTo("author-nickname");
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
                            "uuid": "{push-repo-uuid}",
                            "name": "push-repo",
                            "workspace": {"slug": "push-workspace"}
                        },
                        "push": {
                            "changes": [
                                {
                                    "new": {
                                        "name": "pushed-branch",
                                        "target": {
                                            "hash": "push-commit-hash"
                                        }
                                    }
                                }
                            ]
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("repo:push", jsonNode);

            assertThat(result.externalRepoId()).isEqualTo("push-repo-uuid");
            assertThat(result.repoSlug()).isEqualTo("push-repo");
            assertThat(result.workspaceSlug()).isEqualTo("push-workspace");
            assertThat(result.sourceBranch()).isEqualTo("pushed-branch");
            assertThat(result.commitHash()).isEqualTo("push-commit-hash");
        }

        @Test
        @DisplayName("should handle push event with empty changes array")
        void shouldHandlePushEventWithEmptyChanges() throws Exception {
            String payload = """
                    {
                        "repository": {
                            "uuid": "{repo-uuid}",
                            "name": "repo",
                            "workspace": {"slug": "ws"}
                        },
                        "push": {
                            "changes": []
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("repo:push", jsonNode);

            assertThat(result.sourceBranch()).isNull();
            assertThat(result.commitHash()).isNull();
        }
    }

    @Nested
    @DisplayName("parse() - Comment Events")
    class ParseCommentEvents {

        @Test
        @DisplayName("should parse PR comment created event")
        void shouldParsePrCommentCreatedEvent() throws Exception {
            String payload = """
                    {
                        "repository": {
                            "uuid": "{repo-uuid}",
                            "name": "repo",
                            "workspace": {"slug": "ws"}
                        },
                        "pullrequest": {
                            "id": 10,
                            "source": {"branch": {"name": "branch"}, "commit": {"hash": "hash"}},
                            "destination": {"branch": {"name": "main"}}
                        },
                        "comment": {
                            "id": 12345,
                            "content": {
                                "raw": "This is a comment"
                            },
                            "user": {
                                "uuid": "{comment-user-uuid}",
                                "username": "commenter"
                            }
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("pullrequest:comment_created", jsonNode);

            assertThat(result.commentData()).isNotNull();
            assertThat(result.commentData().commentId()).isEqualTo("12345");
            assertThat(result.commentData().commentBody()).isEqualTo("This is a comment");
            assertThat(result.commentData().commentAuthorId()).isEqualTo("comment-user-uuid");
            assertThat(result.commentData().commentAuthorUsername()).isEqualTo("commenter");
            assertThat(result.commentData().isInlineComment()).isFalse();
        }

        @Test
        @DisplayName("should parse inline comment")
        void shouldParseInlineComment() throws Exception {
            String payload = """
                    {
                        "repository": {
                            "uuid": "{repo-uuid}",
                            "name": "repo",
                            "workspace": {"slug": "ws"}
                        },
                        "pullrequest": {
                            "id": 10,
                            "source": {"branch": {"name": "branch"}, "commit": {"hash": "hash"}},
                            "destination": {"branch": {"name": "main"}}
                        },
                        "comment": {
                            "id": 54321,
                            "content": {"raw": "Inline comment text"},
                            "user": {"uuid": "{user-uuid}", "username": "reviewer"},
                            "inline": {
                                "path": "src/main/java/App.java",
                                "to": 42
                            }
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("pullrequest:comment_created", jsonNode);

            assertThat(result.commentData().isInlineComment()).isTrue();
            assertThat(result.commentData().filePath()).isEqualTo("src/main/java/App.java");
            assertThat(result.commentData().lineNumber()).isEqualTo(42);
        }

        @Test
        @DisplayName("should parse reply comment with parent")
        void shouldParseReplyCommentWithParent() throws Exception {
            String payload = """
                    {
                        "repository": {
                            "uuid": "{repo-uuid}",
                            "name": "repo",
                            "workspace": {"slug": "ws"}
                        },
                        "pullrequest": {
                            "id": 10,
                            "source": {"branch": {"name": "branch"}, "commit": {"hash": "hash"}},
                            "destination": {"branch": {"name": "main"}}
                        },
                        "comment": {
                            "id": 99999,
                            "content": {"raw": "This is a reply"},
                            "user": {"uuid": "{user-uuid}", "username": "replier"},
                            "parent": {
                                "id": 88888
                            }
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("pullrequest:comment_created", jsonNode);

            assertThat(result.commentData().parentCommentId()).isEqualTo("88888");
        }

        @Test
        @DisplayName("should use from line when to line is missing for inline comment")
        void shouldUseFromLineWhenToLineMissing() throws Exception {
            String payload = """
                    {
                        "repository": {
                            "uuid": "{repo-uuid}",
                            "name": "repo",
                            "workspace": {"slug": "ws"}
                        },
                        "pullrequest": {
                            "id": 10,
                            "source": {"branch": {"name": "branch"}, "commit": {"hash": "hash"}},
                            "destination": {"branch": {"name": "main"}}
                        },
                        "comment": {
                            "id": 111,
                            "content": {"raw": "Comment on deleted line"},
                            "user": {"uuid": "{user-uuid}", "username": "user"},
                            "inline": {
                                "path": "deleted-file.java",
                                "from": 15
                            }
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("pullrequest:comment_created", jsonNode);

            assertThat(result.commentData().lineNumber()).isEqualTo(15);
        }

        @Test
        @DisplayName("should not parse comment data for non-comment events")
        void shouldNotParseCommentDataForNonCommentEvents() throws Exception {
            String payload = """
                    {
                        "repository": {
                            "uuid": "{repo-uuid}",
                            "name": "repo",
                            "workspace": {"slug": "ws"}
                        },
                        "pullrequest": {
                            "id": 10,
                            "source": {"branch": {"name": "branch"}, "commit": {"hash": "hash"}},
                            "destination": {"branch": {"name": "main"}}
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("pullrequest:created", jsonNode);

            assertThat(result.commentData()).isNull();
        }
    }

    @Nested
    @DisplayName("parse() - Edge Cases")
    class ParseEdgeCases {

        @Test
        @DisplayName("should handle empty payload")
        void shouldHandleEmptyPayload() throws Exception {
            JsonNode jsonNode = objectMapper.readTree("{}");
            WebhookPayload result = parser.parse("unknown:event", jsonNode);

            assertThat(result.provider()).isEqualTo(EVcsProvider.BITBUCKET_CLOUD);
            assertThat(result.eventType()).isEqualTo("unknown:event");
            assertThat(result.externalRepoId()).isNull();
            assertThat(result.repoSlug()).isNull();
        }

        @Test
        @DisplayName("should strip curly braces from UUID")
        void shouldStripCurlyBracesFromUuid() throws Exception {
            String payload = """
                    {
                        "repository": {
                            "uuid": "{with-curly-braces}",
                            "name": "repo",
                            "workspace": {"slug": "ws"}
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse("repo:push", jsonNode);

            assertThat(result.externalRepoId()).isEqualTo("with-curly-braces");
            assertThat(result.externalRepoId()).doesNotContain("{").doesNotContain("}");
        }

        @Test
        @DisplayName("should handle null event type")
        void shouldHandleNullEventType() throws Exception {
            String payload = """
                    {
                        "repository": {
                            "uuid": "{repo-uuid}",
                            "name": "repo",
                            "workspace": {"slug": "ws"}
                        }
                    }
                    """;

            JsonNode jsonNode = objectMapper.readTree(payload);
            WebhookPayload result = parser.parse(null, jsonNode);

            assertThat(result.eventType()).isNull();
            assertThat(result.commentData()).isNull();
        }
    }
}

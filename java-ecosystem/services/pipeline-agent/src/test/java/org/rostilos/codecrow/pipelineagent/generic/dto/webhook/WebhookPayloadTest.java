package org.rostilos.codecrow.pipelineagent.generic.dto.webhook;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("WebhookPayload")
class WebhookPayloadTest {

    @Nested
    @DisplayName("Record constructor")
    class RecordConstructor {

        @Test
        @DisplayName("should create payload with all fields")
        void shouldCreatePayloadWithAllFields() {
            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITHUB,
                    "pull_request",
                    "repo-123",
                    "my-repo",
                    "my-org",
                    "42",
                    "feature/new",
                    "main",
                    "abc123",
                    null,
                    null,
                    "author-123",
                    "johndoe"
            );
            
            assertThat(payload.provider()).isEqualTo(EVcsProvider.GITHUB);
            assertThat(payload.eventType()).isEqualTo("pull_request");
            assertThat(payload.externalRepoId()).isEqualTo("repo-123");
            assertThat(payload.repoSlug()).isEqualTo("my-repo");
            assertThat(payload.workspaceSlug()).isEqualTo("my-org");
            assertThat(payload.pullRequestId()).isEqualTo("42");
            assertThat(payload.sourceBranch()).isEqualTo("feature/new");
            assertThat(payload.targetBranch()).isEqualTo("main");
            assertThat(payload.commitHash()).isEqualTo("abc123");
            assertThat(payload.prAuthorId()).isEqualTo("author-123");
            assertThat(payload.prAuthorUsername()).isEqualTo("johndoe");
        }

        @Test
        @DisplayName("should create payload without comment data")
        void shouldCreatePayloadWithoutCommentData() {
            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.BITBUCKET_CLOUD,
                    "pullrequest:created",
                    "repo-id",
                    "repo",
                    "workspace",
                    "1",
                    "feature",
                    "main",
                    "hash123",
                    null
            );
            
            assertThat(payload.commentData()).isNull();
        }
    }

    @Nested
    @DisplayName("isPullRequestEvent()")
    class IsPullRequestEvent {

        @Test
        @DisplayName("should return true when pullRequestId is present")
        void shouldReturnTrueWhenPrIdPresent() {
            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITHUB, "pull_request", "id", "repo", "org",
                    "42", null, null, null, null);
            
            assertThat(payload.isPullRequestEvent()).isTrue();
        }

        @Test
        @DisplayName("should return false when pullRequestId is null")
        void shouldReturnFalseWhenPrIdNull() {
            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITHUB, "push", "id", "repo", "org",
                    null, null, null, null, null);
            
            assertThat(payload.isPullRequestEvent()).isFalse();
        }
    }

    @Nested
    @DisplayName("isPushEvent()")
    class IsPushEvent {

        @Test
        @DisplayName("should return true for push event type")
        void shouldReturnTrueForPushEventType() {
            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITHUB, "push", "id", "repo", "org",
                    null, null, "main", "hash", null);
            
            assertThat(payload.isPushEvent()).isTrue();
        }

        @Test
        @DisplayName("should return true for commit status event")
        void shouldReturnTrueForCommitStatusEvent() {
            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.BITBUCKET_CLOUD, "repo:commit_status_updated", "id", "repo", "org",
                    null, null, null, null, null);
            
            assertThat(payload.isPushEvent()).isTrue();
        }

        @Test
        @DisplayName("should return false for non-push event")
        void shouldReturnFalseForNonPushEvent() {
            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITHUB, "pull_request", "id", "repo", "org",
                    "42", null, null, null, null);
            
            assertThat(payload.isPushEvent()).isFalse();
        }
    }

    @Nested
    @DisplayName("isCommentEvent()")
    class IsCommentEvent {

        @Test
        @DisplayName("should return true when commentData is present")
        void shouldReturnTrueWhenCommentDataPresent() {
            WebhookPayload.CommentData commentData = new WebhookPayload.CommentData(
                    "comment-1", "Hello", "user-1", "user", null, false, null, null);
            
            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITHUB, "issue_comment", "id", "repo", "org",
                    "42", null, null, null, null, commentData);
            
            assertThat(payload.isCommentEvent()).isTrue();
        }

        @Test
        @DisplayName("should return false when commentData is null")
        void shouldReturnFalseWhenCommentDataNull() {
            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITHUB, "pull_request", "id", "repo", "org",
                    "42", null, null, null, null);
            
            assertThat(payload.isCommentEvent()).isFalse();
        }
    }

    @Nested
    @DisplayName("getFullRepoName()")
    class GetFullRepoName {

        @Test
        @DisplayName("should return workspace/repo format")
        void shouldReturnWorkspaceRepoFormat() {
            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITHUB, "push", "id", "my-repo", "my-org",
                    null, null, null, null, null);
            
            assertThat(payload.getFullRepoName()).isEqualTo("my-org/my-repo");
        }

        @Test
        @DisplayName("should return just repo when workspace is null")
        void shouldReturnJustRepoWhenWorkspaceNull() {
            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITHUB, "push", "id", "my-repo", null,
                    null, null, null, null, null);
            
            assertThat(payload.getFullRepoName()).isEqualTo("my-repo");
        }
    }

    @Nested
    @DisplayName("withEnrichedPrDetails()")
    class WithEnrichedPrDetails {

        @Test
        @DisplayName("should create new payload with enriched details")
        void shouldCreateNewPayloadWithEnrichedDetails() {
            WebhookPayload original = new WebhookPayload(
                    EVcsProvider.GITHUB, "issue_comment", "id", "repo", "org",
                    "42", null, null, null, null);
            
            WebhookPayload enriched = original.withEnrichedPrDetails("feature/new", "main", "abc123");
            
            assertThat(enriched.sourceBranch()).isEqualTo("feature/new");
            assertThat(enriched.targetBranch()).isEqualTo("main");
            assertThat(enriched.commitHash()).isEqualTo("abc123");
        }

        @Test
        @DisplayName("should preserve existing values when enriched values are null")
        void shouldPreserveExistingValuesWhenEnrichedNull() {
            WebhookPayload original = new WebhookPayload(
                    EVcsProvider.GITHUB, "pull_request", "id", "repo", "org",
                    "42", "old-source", "old-target", "old-hash", null);
            
            WebhookPayload enriched = original.withEnrichedPrDetails(null, null, null);
            
            assertThat(enriched.sourceBranch()).isEqualTo("old-source");
            assertThat(enriched.targetBranch()).isEqualTo("old-target");
            assertThat(enriched.commitHash()).isEqualTo("old-hash");
        }
    }

    @Nested
    @DisplayName("withPrAuthor()")
    class WithPrAuthor {

        @Test
        @DisplayName("should create new payload with PR author info")
        void shouldCreateNewPayloadWithPrAuthorInfo() {
            WebhookPayload original = new WebhookPayload(
                    EVcsProvider.GITHUB, "pull_request", "id", "repo", "org",
                    "42", "feature", "main", "hash", null);
            
            WebhookPayload withAuthor = original.withPrAuthor("author-123", "johndoe");
            
            assertThat(withAuthor.prAuthorId()).isEqualTo("author-123");
            assertThat(withAuthor.prAuthorUsername()).isEqualTo("johndoe");
        }
    }

    @Nested
    @DisplayName("isCommentByPrAuthor()")
    class IsCommentByPrAuthor {

        @Test
        @DisplayName("should return true when comment author matches PR author by ID")
        void shouldReturnTrueWhenAuthorMatchesById() {
            WebhookPayload.CommentData commentData = new WebhookPayload.CommentData(
                    "comment-1", "Hello", "author-123", "johndoe", null, false, null, null);
            
            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITHUB, "issue_comment", "id", "repo", "org",
                    "42", null, null, null, null, commentData, "author-123", "johndoe");
            
            assertThat(payload.isCommentByPrAuthor()).isTrue();
        }

        @Test
        @DisplayName("should return false when comment author differs")
        void shouldReturnFalseWhenAuthorDiffers() {
            WebhookPayload.CommentData commentData = new WebhookPayload.CommentData(
                    "comment-1", "Hello", "reviewer-456", "reviewer", null, false, null, null);
            
            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITHUB, "issue_comment", "id", "repo", "org",
                    "42", null, null, null, null, commentData, "author-123", "johndoe");
            
            assertThat(payload.isCommentByPrAuthor()).isFalse();
        }

        @Test
        @DisplayName("should return false when no comment data")
        void shouldReturnFalseWhenNoCommentData() {
            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITHUB, "pull_request", "id", "repo", "org",
                    "42", null, null, null, null, null, "author-123", "johndoe");
            
            assertThat(payload.isCommentByPrAuthor()).isFalse();
        }
    }

    @Nested
    @DisplayName("CommentData")
    class CommentDataTests {

        @Test
        @DisplayName("should create comment data with all fields")
        void shouldCreateCommentDataWithAllFields() {
            WebhookPayload.CommentData commentData = new WebhookPayload.CommentData(
                    "comment-123",
                    "/codecrow analyze",
                    "user-1",
                    "johndoe",
                    "parent-1",
                    true,
                    "src/main/java/Test.java",
                    42
            );
            
            assertThat(commentData.commentId()).isEqualTo("comment-123");
            assertThat(commentData.commentBody()).isEqualTo("/codecrow analyze");
            assertThat(commentData.commentAuthorId()).isEqualTo("user-1");
            assertThat(commentData.commentAuthorUsername()).isEqualTo("johndoe");
            assertThat(commentData.parentCommentId()).isEqualTo("parent-1");
            assertThat(commentData.isInlineComment()).isTrue();
            assertThat(commentData.filePath()).isEqualTo("src/main/java/Test.java");
            assertThat(commentData.lineNumber()).isEqualTo(42);
        }

        @Test
        @DisplayName("should parse analyze command")
        void shouldParseAnalyzeCommand() {
            WebhookPayload.CommentData commentData = new WebhookPayload.CommentData(
                    "id", "/codecrow analyze", "user", "name", null, false, null, null);
            
            WebhookPayload.CodecrowCommand command = commentData.parseCommand();
            
            assertThat(command).isNotNull();
            assertThat(command.type()).isEqualTo(WebhookPayload.CommandType.ANALYZE);
        }

        @Test
        @DisplayName("should parse summarize command")
        void shouldParseSummarizeCommand() {
            WebhookPayload.CommentData commentData = new WebhookPayload.CommentData(
                    "id", "/codecrow summarize", "user", "name", null, false, null, null);
            
            WebhookPayload.CodecrowCommand command = commentData.parseCommand();
            
            assertThat(command).isNotNull();
            assertThat(command.type()).isEqualTo(WebhookPayload.CommandType.SUMMARIZE);
        }

        @Test
        @DisplayName("should parse review command")
        void shouldParseReviewCommand() {
            WebhookPayload.CommentData commentData = new WebhookPayload.CommentData(
                    "id", "/codecrow review", "user", "name", null, false, null, null);
            
            WebhookPayload.CodecrowCommand command = commentData.parseCommand();
            
            assertThat(command).isNotNull();
            assertThat(command.type()).isEqualTo(WebhookPayload.CommandType.REVIEW);
        }

        @Test
        @DisplayName("should parse ask command with arguments")
        void shouldParseAskCommandWithArguments() {
            WebhookPayload.CommentData commentData = new WebhookPayload.CommentData(
                    "id", "/codecrow ask What is the purpose of this code?", "user", "name", null, false, null, null);
            
            WebhookPayload.CodecrowCommand command = commentData.parseCommand();
            
            assertThat(command).isNotNull();
            assertThat(command.type()).isEqualTo(WebhookPayload.CommandType.ASK);
            assertThat(command.arguments()).isEqualTo("What is the purpose of this code?");
        }

        @Test
        @DisplayName("should return null for ask command without arguments")
        void shouldReturnNullForAskWithoutArguments() {
            WebhookPayload.CommentData commentData = new WebhookPayload.CommentData(
                    "id", "/codecrow ask", "user", "name", null, false, null, null);
            
            WebhookPayload.CodecrowCommand command = commentData.parseCommand();
            
            assertThat(command).isNull();
        }

        @Test
        @DisplayName("should return null for non-codecrow command")
        void shouldReturnNullForNonCodecrowCommand() {
            WebhookPayload.CommentData commentData = new WebhookPayload.CommentData(
                    "id", "Just a regular comment", "user", "name", null, false, null, null);
            
            WebhookPayload.CodecrowCommand command = commentData.parseCommand();
            
            assertThat(command).isNull();
        }

        @Test
        @DisplayName("should return null for unknown command")
        void shouldReturnNullForUnknownCommand() {
            WebhookPayload.CommentData commentData = new WebhookPayload.CommentData(
                    "id", "/codecrow unknown", "user", "name", null, false, null, null);
            
            WebhookPayload.CodecrowCommand command = commentData.parseCommand();
            
            assertThat(command).isNull();
        }

        @Test
        @DisplayName("should return null for empty comment body")
        void shouldReturnNullForEmptyCommentBody() {
            WebhookPayload.CommentData commentData = new WebhookPayload.CommentData(
                    "id", "", "user", "name", null, false, null, null);
            
            WebhookPayload.CodecrowCommand command = commentData.parseCommand();
            
            assertThat(command).isNull();
        }

        @Test
        @DisplayName("should return null for null comment body")
        void shouldReturnNullForNullCommentBody() {
            WebhookPayload.CommentData commentData = new WebhookPayload.CommentData(
                    "id", null, "user", "name", null, false, null, null);
            
            WebhookPayload.CodecrowCommand command = commentData.parseCommand();
            
            assertThat(command).isNull();
        }
    }

    @Nested
    @DisplayName("CodecrowCommand")
    class CodecrowCommandTests {

        @Test
        @DisplayName("should get type string")
        void shouldGetTypeString() {
            WebhookPayload.CodecrowCommand command = new WebhookPayload.CodecrowCommand(
                    WebhookPayload.CommandType.ANALYZE, null);
            
            assertThat(command.getTypeString()).isEqualTo("analyze");
        }
    }

    @Nested
    @DisplayName("hasCodecrowCommand()")
    class HasCodecrowCommand {

        @Test
        @DisplayName("should return true when valid command present")
        void shouldReturnTrueWhenValidCommandPresent() {
            WebhookPayload.CommentData commentData = new WebhookPayload.CommentData(
                    "id", "/codecrow analyze", "user", "name", null, false, null, null);
            
            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITHUB, "issue_comment", "id", "repo", "org",
                    "42", null, null, null, null, commentData);
            
            assertThat(payload.hasCodecrowCommand()).isTrue();
        }

        @Test
        @DisplayName("should return false when no valid command")
        void shouldReturnFalseWhenNoValidCommand() {
            WebhookPayload.CommentData commentData = new WebhookPayload.CommentData(
                    "id", "regular comment", "user", "name", null, false, null, null);
            
            WebhookPayload payload = new WebhookPayload(
                    EVcsProvider.GITHUB, "issue_comment", "id", "repo", "org",
                    "42", null, null, null, null, commentData);
            
            assertThat(payload.hasCodecrowCommand()).isFalse();
        }
    }
}

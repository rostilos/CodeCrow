package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("BitbucketPullRequest")
class BitbucketPullRequestTest {

    @Test
    @DisplayName("should create with all fields")
    void shouldCreateWithAllFields() {
        BitbucketAccount author = new BitbucketAccount();
        author.username = "pr-author";
        
        BitbucketPullRequest.BitbucketSummary summary = new BitbucketPullRequest.BitbucketSummary(
                "Raw summary", "markup", "<p>HTML</p>"
        );
        
        BitbucketPullRequest pr = new BitbucketPullRequest(
                123L,
                "Add new feature",
                "OPEN",
                author,
                null, // source
                null, // destination
                "2024-01-10T10:00:00Z",
                "2024-01-15T15:00:00Z",
                5,
                2,
                true,
                List.of(),
                List.of(),
                summary,
                null, // rendered
                null, // mergeCommit
                null, // closedBy
                null, // reason
                false,
                false
        );
        
        assertThat(pr.id()).isEqualTo(123L);
        assertThat(pr.title()).isEqualTo("Add new feature");
        assertThat(pr.state()).isEqualTo("OPEN");
        assertThat(pr.author()).isNotNull();
        assertThat(pr.author().getUsername()).isEqualTo("pr-author");
        assertThat(pr.createdOn()).isEqualTo("2024-01-10T10:00:00Z");
        assertThat(pr.updatedOn()).isEqualTo("2024-01-15T15:00:00Z");
        assertThat(pr.commentCount()).isEqualTo(5);
        assertThat(pr.taskCount()).isEqualTo(2);
        assertThat(pr.closeSourceBranch()).isTrue();
        assertThat(pr.reviewers()).isEmpty();
        assertThat(pr.participants()).isEmpty();
        assertThat(pr.summary()).isNotNull();
        assertThat(pr.draft()).isFalse();
        assertThat(pr.queued()).isFalse();
    }

    @Test
    @DisplayName("should create with minimal fields")
    void shouldCreateWithMinimalFields() {
        BitbucketPullRequest pr = new BitbucketPullRequest(
                1L, null, null, null, null, null,
                null, null, 0, 0, false, null, null,
                null, null, null, null, null, false, false
        );
        
        assertThat(pr.id()).isEqualTo(1L);
        assertThat(pr.title()).isNull();
        assertThat(pr.state()).isNull();
    }

    @Nested
    @DisplayName("BitbucketSummary nested record")
    class SummaryRecord {

        @Test
        @DisplayName("should create with all fields")
        void shouldCreateWithAllFields() {
            BitbucketPullRequest.BitbucketSummary summary = new BitbucketPullRequest.BitbucketSummary(
                    "raw text", "markdown", "<p>html</p>"
            );
            
            assertThat(summary.raw()).isEqualTo("raw text");
            assertThat(summary.markup()).isEqualTo("markdown");
            assertThat(summary.html()).isEqualTo("<p>html</p>");
        }

        @Test
        @DisplayName("should be equal for same values")
        void shouldBeEqualForSameValues() {
            BitbucketPullRequest.BitbucketSummary s1 = new BitbucketPullRequest.BitbucketSummary("raw", "md", "html");
            BitbucketPullRequest.BitbucketSummary s2 = new BitbucketPullRequest.BitbucketSummary("raw", "md", "html");
            
            assertThat(s1).isEqualTo(s2);
        }
    }

    @Nested
    @DisplayName("BitbucketRenderedContent nested record")
    class RenderedContentRecord {

        @Test
        @DisplayName("should create with all fields")
        void shouldCreateWithAllFields() {
            BitbucketPullRequest.BitbucketSummary title = new BitbucketPullRequest.BitbucketSummary("title", "md", "html");
            BitbucketPullRequest.BitbucketSummary description = new BitbucketPullRequest.BitbucketSummary("desc", "md", "html");
            BitbucketPullRequest.BitbucketSummary reason = new BitbucketPullRequest.BitbucketSummary("reason", "md", "html");
            
            BitbucketPullRequest.BitbucketRenderedContent rendered = new BitbucketPullRequest.BitbucketRenderedContent(
                    title, description, reason
            );
            
            assertThat(rendered.title()).isEqualTo(title);
            assertThat(rendered.description()).isEqualTo(description);
            assertThat(rendered.reason()).isEqualTo(reason);
        }
    }

    @Nested
    @DisplayName("BitbucketCommit nested record")
    class CommitRecord {

        @Test
        @DisplayName("should create with hash")
        void shouldCreateWithHash() {
            BitbucketPullRequest.BitbucketCommit commit = new BitbucketPullRequest.BitbucketCommit("abc123def");
            
            assertThat(commit.hash()).isEqualTo("abc123def");
        }

        @Test
        @DisplayName("should be equal for same hash")
        void shouldBeEqualForSameHash() {
            BitbucketPullRequest.BitbucketCommit c1 = new BitbucketPullRequest.BitbucketCommit("hash");
            BitbucketPullRequest.BitbucketCommit c2 = new BitbucketPullRequest.BitbucketCommit("hash");
            
            assertThat(c1).isEqualTo(c2);
        }
    }
}

package org.rostilos.codecrow.vcsclient.bitbucket.model.comment;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("BitbucketSummarizeComment")
class BitbucketSummarizeCommentTest {

    @Test
    @DisplayName("should create with content")
    void shouldCreateWithContent() {
        BitbucketCommentContent content = new BitbucketCommentContent("Summary content");
        BitbucketSummarizeComment comment = new BitbucketSummarizeComment(content);
        
        assertThat(comment.content()).isNotNull();
        assertThat(comment.content().raw()).isEqualTo("Summary content");
    }

    @Test
    @DisplayName("should handle null content")
    void shouldHandleNullContent() {
        BitbucketSummarizeComment comment = new BitbucketSummarizeComment(null);
        
        assertThat(comment.content()).isNull();
    }

    @Test
    @DisplayName("should wrap nested content")
    void shouldWrapNestedContent() {
        String rawContent = "## PR Summary\n\nThis PR adds feature X.";
        BitbucketCommentContent content = new BitbucketCommentContent(rawContent);
        BitbucketSummarizeComment comment = new BitbucketSummarizeComment(content);
        
        assertThat(comment.content().raw()).isEqualTo(rawContent);
    }
}

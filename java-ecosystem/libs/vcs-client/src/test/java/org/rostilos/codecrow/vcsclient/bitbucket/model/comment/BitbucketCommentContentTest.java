package org.rostilos.codecrow.vcsclient.bitbucket.model.comment;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("BitbucketCommentContent")
class BitbucketCommentContentTest {

    @Test
    @DisplayName("should create with raw content")
    void shouldCreateWithRawContent() {
        BitbucketCommentContent content = new BitbucketCommentContent("Test comment content");
        
        assertThat(content.raw()).isEqualTo("Test comment content");
    }

    @Test
    @DisplayName("should handle null raw content")
    void shouldHandleNullRawContent() {
        BitbucketCommentContent content = new BitbucketCommentContent(null);
        
        assertThat(content.raw()).isNull();
    }

    @Test
    @DisplayName("should handle empty raw content")
    void shouldHandleEmptyRawContent() {
        BitbucketCommentContent content = new BitbucketCommentContent("");
        
        assertThat(content.raw()).isEmpty();
    }

    @Test
    @DisplayName("should handle multiline content")
    void shouldHandleMultilineContent() {
        String multiline = "Line 1\nLine 2\nLine 3";
        BitbucketCommentContent content = new BitbucketCommentContent(multiline);
        
        assertThat(content.raw()).isEqualTo(multiline);
        assertThat(content.raw()).contains("\n");
    }

    @Test
    @DisplayName("should handle markdown content")
    void shouldHandleMarkdownContent() {
        String markdown = "## Heading\n- Item 1\n- Item 2\n**bold** and *italic*";
        BitbucketCommentContent content = new BitbucketCommentContent(markdown);
        
        assertThat(content.raw()).isEqualTo(markdown);
    }
}

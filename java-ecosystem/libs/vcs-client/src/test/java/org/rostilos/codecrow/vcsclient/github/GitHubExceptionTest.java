package org.rostilos.codecrow.vcsclient.github;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

@DisplayName("GitHubException")
class GitHubExceptionTest {

    @Test
    @DisplayName("should create exception with message only")
    void shouldCreateExceptionWithMessageOnly() {
        GitHubException exception = new GitHubException("API error");
        
        assertThat(exception.getMessage()).isEqualTo("API error");
        assertThat(exception.getStatusCode()).isEqualTo(-1);
        assertThat(exception.getResponseBody()).isNull();
    }

    @Test
    @DisplayName("should create exception with message and cause")
    void shouldCreateExceptionWithMessageAndCause() {
        Throwable cause = new RuntimeException("Root cause");
        GitHubException exception = new GitHubException("API error", cause);
        
        assertThat(exception.getMessage()).isEqualTo("API error");
        assertThat(exception.getCause()).isEqualTo(cause);
        assertThat(exception.getStatusCode()).isEqualTo(-1);
    }

    @Test
    @DisplayName("should create exception with operation, status code, and response")
    void shouldCreateExceptionWithOperationStatusAndResponse() {
        GitHubException exception = new GitHubException("createComment", 403, "Forbidden");
        
        assertThat(exception.getMessage()).contains("createComment");
        assertThat(exception.getMessage()).contains("403");
        assertThat(exception.getMessage()).contains("Forbidden");
        assertThat(exception.getStatusCode()).isEqualTo(403);
        assertThat(exception.getResponseBody()).isEqualTo("Forbidden");
    }

    @Test
    @DisplayName("should extend VcsClientException")
    void shouldExtendVcsClientException() {
        GitHubException exception = new GitHubException("Error");
        
        assertThat(exception).isInstanceOf(org.rostilos.codecrow.vcsclient.VcsClientException.class);
    }

    @Test
    @DisplayName("should be throwable")
    void shouldBeThrowable() {
        assertThatThrownBy(() -> {
            throw new GitHubException("Test error");
        })
        .isInstanceOf(GitHubException.class)
        .hasMessage("Test error");
    }

    @Test
    @DisplayName("should format message with API error details")
    void shouldFormatMessageWithApiErrorDetails() {
        GitHubException exception = new GitHubException("getPullRequest", 404, "Not Found");
        
        assertThat(exception.getMessage()).isEqualTo("GitHub API error during getPullRequest: HTTP 404 - Not Found");
    }
}

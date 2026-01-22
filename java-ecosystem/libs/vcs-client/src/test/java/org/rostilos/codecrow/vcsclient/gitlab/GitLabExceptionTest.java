package org.rostilos.codecrow.vcsclient.gitlab;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

@DisplayName("GitLabException")
class GitLabExceptionTest {

    @Test
    @DisplayName("should create exception with operation, status code, and response")
    void shouldCreateExceptionWithOperationStatusAndResponse() {
        GitLabException exception = new GitLabException("createNote", 403, "Forbidden");
        
        assertThat(exception.getMessage()).contains("createNote");
        assertThat(exception.getMessage()).contains("403");
        assertThat(exception.getMessage()).contains("Forbidden");
        assertThat(exception.getStatusCode()).isEqualTo(403);
        assertThat(exception.getResponseBody()).isEqualTo("Forbidden");
    }

    @Test
    @DisplayName("should create exception with message only")
    void shouldCreateExceptionWithMessageOnly() {
        GitLabException exception = new GitLabException("API error");
        
        assertThat(exception.getMessage()).isEqualTo("API error");
        assertThat(exception.getStatusCode()).isEqualTo(-1);
        assertThat(exception.getResponseBody()).isNull();
    }

    @Test
    @DisplayName("should create exception with message and cause")
    void shouldCreateExceptionWithMessageAndCause() {
        Throwable cause = new RuntimeException("Root cause");
        GitLabException exception = new GitLabException("API error", cause);
        
        assertThat(exception.getMessage()).isEqualTo("API error");
        assertThat(exception.getCause()).isEqualTo(cause);
        assertThat(exception.getStatusCode()).isEqualTo(-1);
    }

    @Test
    @DisplayName("should extend RuntimeException")
    void shouldExtendRuntimeException() {
        GitLabException exception = new GitLabException("Error");
        
        assertThat(exception).isInstanceOf(RuntimeException.class);
    }

    @Test
    @DisplayName("should be throwable")
    void shouldBeThrowable() {
        assertThatThrownBy(() -> {
            throw new GitLabException("Test error");
        })
        .isInstanceOf(GitLabException.class)
        .hasMessage("Test error");
    }

    @Test
    @DisplayName("should format message with API error details")
    void shouldFormatMessageWithApiErrorDetails() {
        GitLabException exception = new GitLabException("getMergeRequest", 404, "Not Found");
        
        assertThat(exception.getMessage()).isEqualTo("GitLab getMergeRequest failed: 404 - Not Found");
    }
}

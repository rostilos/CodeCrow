package org.rostilos.codecrow.mcp.gitlab;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("GitLabException")
class GitLabExceptionTest {

    @Nested
    @DisplayName("Constructor with message")
    class ConstructorWithMessage {

        @Test
        @DisplayName("should create exception with message")
        void shouldCreateExceptionWithMessage() {
            String message = "GitLab API error";
            
            GitLabException exception = new GitLabException(message);
            
            assertThat(exception.getMessage()).isEqualTo(message);
            assertThat(exception.getCause()).isNull();
        }

        @Test
        @DisplayName("should create exception with null message")
        void shouldCreateExceptionWithNullMessage() {
            GitLabException exception = new GitLabException(null);
            
            assertThat(exception.getMessage()).isNull();
        }
    }

    @Nested
    @DisplayName("Constructor with message and cause")
    class ConstructorWithMessageAndCause {

        @Test
        @DisplayName("should create exception with message and cause")
        void shouldCreateExceptionWithMessageAndCause() {
            String message = "Failed to fetch MR";
            IOException cause = new IOException("Network error");
            
            GitLabException exception = new GitLabException(message, cause);
            
            assertThat(exception.getMessage()).isEqualTo(message);
            assertThat(exception.getCause()).isEqualTo(cause);
        }

        @Test
        @DisplayName("should create exception with null cause")
        void shouldCreateExceptionWithNullCause() {
            GitLabException exception = new GitLabException("Error", null);
            
            assertThat(exception.getMessage()).isEqualTo("Error");
            assertThat(exception.getCause()).isNull();
        }
    }

    @Test
    @DisplayName("should be RuntimeException")
    void shouldBeRuntimeException() {
        GitLabException exception = new GitLabException("Test");
        
        assertThat(exception).isInstanceOf(RuntimeException.class);
    }
    
    private static class IOException extends Exception {
        public IOException(String message) {
            super(message);
        }
    }
}

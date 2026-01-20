package org.rostilos.codecrow.mcp.github;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("GitHubException")
class GitHubExceptionTest {

    @Nested
    @DisplayName("Constructor with message")
    class ConstructorWithMessage {

        @Test
        @DisplayName("should create exception with message")
        void shouldCreateExceptionWithMessage() {
            String message = "GitHub API rate limit exceeded";
            
            GitHubException exception = new GitHubException(message);
            
            assertThat(exception.getMessage()).isEqualTo(message);
            assertThat(exception.getCause()).isNull();
        }

        @Test
        @DisplayName("should create exception with null message")
        void shouldCreateExceptionWithNullMessage() {
            GitHubException exception = new GitHubException(null);
            
            assertThat(exception.getMessage()).isNull();
        }
    }

    @Nested
    @DisplayName("Constructor with message and cause")
    class ConstructorWithMessageAndCause {

        @Test
        @DisplayName("should create exception with message and cause")
        void shouldCreateExceptionWithMessageAndCause() {
            String message = "Failed to fetch PR";
            RuntimeException cause = new RuntimeException("Connection timeout");
            
            GitHubException exception = new GitHubException(message, cause);
            
            assertThat(exception.getMessage()).isEqualTo(message);
            assertThat(exception.getCause()).isEqualTo(cause);
        }

        @Test
        @DisplayName("should create exception with null cause")
        void shouldCreateExceptionWithNullCause() {
            GitHubException exception = new GitHubException("Error", null);
            
            assertThat(exception.getMessage()).isEqualTo("Error");
            assertThat(exception.getCause()).isNull();
        }
    }

    @Test
    @DisplayName("should be RuntimeException")
    void shouldBeRuntimeException() {
        GitHubException exception = new GitHubException("Test");
        
        assertThat(exception).isInstanceOf(RuntimeException.class);
    }
}

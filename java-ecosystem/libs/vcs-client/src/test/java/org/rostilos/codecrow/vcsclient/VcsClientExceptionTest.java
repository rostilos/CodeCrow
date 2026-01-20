package org.rostilos.codecrow.vcsclient;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

@DisplayName("VcsClientException")
class VcsClientExceptionTest {

    @Nested
    @DisplayName("Constructors")
    class Constructors {

        @Test
        @DisplayName("should create exception with message")
        void shouldCreateExceptionWithMessage() {
            VcsClientException exception = new VcsClientException("Test error message");
            
            assertThat(exception.getMessage()).isEqualTo("Test error message");
            assertThat(exception.getCause()).isNull();
        }

        @Test
        @DisplayName("should create exception with message and cause")
        void shouldCreateExceptionWithMessageAndCause() {
            RuntimeException cause = new RuntimeException("Root cause");
            VcsClientException exception = new VcsClientException("Test error message", cause);
            
            assertThat(exception.getMessage()).isEqualTo("Test error message");
            assertThat(exception.getCause()).isEqualTo(cause);
        }
    }

    @Nested
    @DisplayName("Exception Behavior")
    class ExceptionBehavior {

        @Test
        @DisplayName("should be throwable")
        void shouldBeThrowable() {
            assertThatThrownBy(() -> {
                throw new VcsClientException("Test exception");
            }).isInstanceOf(VcsClientException.class)
              .hasMessage("Test exception");
        }

        @Test
        @DisplayName("should be a RuntimeException")
        void shouldBeRuntimeException() {
            VcsClientException exception = new VcsClientException("Test");
            
            assertThat(exception).isInstanceOf(RuntimeException.class);
        }

        @Test
        @DisplayName("should preserve cause chain")
        void shouldPreserveCauseChain() {
            IllegalArgumentException rootCause = new IllegalArgumentException("Invalid arg");
            RuntimeException intermediateCause = new RuntimeException("Intermediate", rootCause);
            VcsClientException exception = new VcsClientException("VCS error", intermediateCause);
            
            assertThat(exception.getCause()).isEqualTo(intermediateCause);
            assertThat(exception.getCause().getCause()).isEqualTo(rootCause);
        }
    }
}

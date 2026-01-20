package org.rostilos.codecrow.mcp.bitbucket;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

@DisplayName("BitbucketCloudException")
class BitbucketCloudExceptionTest {

    @Nested
    @DisplayName("Constructor")
    class Constructor {

        @Test
        @DisplayName("should create exception with message")
        void shouldCreateExceptionWithMessage() {
            BitbucketCloudException exception = new BitbucketCloudException("Test error message");
            
            assertThat(exception.getMessage()).isEqualTo("Test error message");
        }
    }

    @Nested
    @DisplayName("Exception Behavior")
    class ExceptionBehavior {

        @Test
        @DisplayName("should be throwable")
        void shouldBeThrowable() {
            assertThatThrownBy(() -> {
                throw new BitbucketCloudException("Test exception");
            }).isInstanceOf(BitbucketCloudException.class)
              .hasMessage("Test exception");
        }

        @Test
        @DisplayName("should be a RuntimeException")
        void shouldBeRuntimeException() {
            BitbucketCloudException exception = new BitbucketCloudException("Test");
            
            assertThat(exception).isInstanceOf(RuntimeException.class);
        }
    }
}

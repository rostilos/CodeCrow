package org.rostilos.codecrow.vcsclient.bitbucket.cloud;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

@DisplayName("BitbucketCloudException")
class BitbucketCloudExceptionTest {

    @Test
    @DisplayName("should create exception with message")
    void shouldCreateExceptionWithMessage() {
        BitbucketCloudException exception = new BitbucketCloudException("API error");
        
        assertThat(exception.getMessage()).isEqualTo("API error");
    }

    @Test
    @DisplayName("should be throwable")
    void shouldBeThrowable() {
        assertThatThrownBy(() -> {
            throw new BitbucketCloudException("Test error");
        })
        .isInstanceOf(BitbucketCloudException.class)
        .hasMessage("Test error");
    }

    @Test
    @DisplayName("should extend RuntimeException")
    void shouldExtendRuntimeException() {
        BitbucketCloudException exception = new BitbucketCloudException("Error");
        
        assertThat(exception).isInstanceOf(RuntimeException.class);
    }
}

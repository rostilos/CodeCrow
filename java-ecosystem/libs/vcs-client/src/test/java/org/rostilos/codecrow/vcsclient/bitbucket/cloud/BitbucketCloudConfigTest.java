package org.rostilos.codecrow.vcsclient.bitbucket.cloud;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("BitbucketCloudConfig")
class BitbucketCloudConfigTest {

    @Test
    @DisplayName("should have correct API base URL")
    void shouldHaveCorrectApiBaseUrl() {
        assertThat(BitbucketCloudConfig.BITBUCKET_API_BASE)
                .isEqualTo("https://api.bitbucket.org/2.0");
    }

    @Test
    @DisplayName("API base URL should be HTTPS")
    void apiBaseUrlShouldBeHttps() {
        assertThat(BitbucketCloudConfig.BITBUCKET_API_BASE).startsWith("https://");
    }

    @Test
    @DisplayName("API base URL should target bitbucket.org")
    void apiBaseUrlShouldTargetBitbucket() {
        assertThat(BitbucketCloudConfig.BITBUCKET_API_BASE).contains("bitbucket.org");
    }

    @Test
    @DisplayName("API base URL should use version 2.0")
    void apiBaseUrlShouldUseVersion2() {
        assertThat(BitbucketCloudConfig.BITBUCKET_API_BASE).endsWith("/2.0");
    }
}

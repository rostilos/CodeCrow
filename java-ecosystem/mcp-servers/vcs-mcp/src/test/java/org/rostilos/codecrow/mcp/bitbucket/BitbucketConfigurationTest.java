package org.rostilos.codecrow.mcp.bitbucket;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("BitbucketConfiguration")
class BitbucketConfigurationTest {

    @Nested
    @DisplayName("Constructor")
    class Constructor {

        @Test
        @DisplayName("should create configuration with workspace and repository")
        void shouldCreateConfigurationWithWorkspaceAndRepository() {
            BitbucketConfiguration config = new BitbucketConfiguration("my-workspace", "my-repo");
            
            assertThat(config.getWorkspace()).isEqualTo("my-workspace");
            assertThat(config.getRepository()).isEqualTo("my-repo");
        }

        @Test
        @DisplayName("should handle null values")
        void shouldHandleNullValues() {
            BitbucketConfiguration config = new BitbucketConfiguration(null, null);
            
            assertThat(config.getWorkspace()).isNull();
            assertThat(config.getRepository()).isNull();
        }
    }

    @Nested
    @DisplayName("Getters")
    class Getters {

        @Test
        @DisplayName("should get workspace")
        void shouldGetWorkspace() {
            BitbucketConfiguration config = new BitbucketConfiguration("test-workspace", "repo");
            
            assertThat(config.getWorkspace()).isEqualTo("test-workspace");
        }

        @Test
        @DisplayName("should get repository")
        void shouldGetRepository() {
            BitbucketConfiguration config = new BitbucketConfiguration("workspace", "test-repo");
            
            assertThat(config.getRepository()).isEqualTo("test-repo");
        }
    }
}

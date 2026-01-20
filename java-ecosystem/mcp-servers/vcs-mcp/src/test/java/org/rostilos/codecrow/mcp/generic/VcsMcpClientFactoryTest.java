package org.rostilos.codecrow.mcp.generic;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

@DisplayName("VcsMcpClientFactory Tests")
class VcsMcpClientFactoryTest {

    private final VcsMcpClientFactory factory = new VcsMcpClientFactory();

    @Nested
    @DisplayName("Provider constants tests")
    class ProviderConstantsTests {

        @Test
        @DisplayName("Should have correct Bitbucket provider constant")
        void shouldHaveCorrectBitbucketProviderConstant() {
            assertThat(VcsMcpClientFactory.PROVIDER_BITBUCKET).isEqualTo("bitbucket");
        }

        @Test
        @DisplayName("Should have correct GitHub provider constant")
        void shouldHaveCorrectGitHubProviderConstant() {
            assertThat(VcsMcpClientFactory.PROVIDER_GITHUB).isEqualTo("github");
        }

        @Test
        @DisplayName("Should have correct GitLab provider constant")
        void shouldHaveCorrectGitLabProviderConstant() {
            assertThat(VcsMcpClientFactory.PROVIDER_GITLAB).isEqualTo("gitlab");
        }
    }

    @Nested
    @DisplayName("createClient(String provider) tests")
    class CreateClientWithProviderTests {

        @Test
        @DisplayName("Should throw exception for unsupported provider")
        void shouldThrowExceptionForUnsupportedProvider() {
            assertThatThrownBy(() -> factory.createClient("unsupported"))
                    .isInstanceOf(IllegalArgumentException.class)
                    .hasMessage("Unsupported VCS provider: unsupported");
        }

        @Test
        @DisplayName("Should throw exception for empty provider")
        void shouldThrowExceptionForEmptyProvider() {
            assertThatThrownBy(() -> factory.createClient(""))
                    .isInstanceOf(IllegalArgumentException.class)
                    .hasMessageContaining("Unsupported VCS provider");
        }

        @Test
        @DisplayName("Should throw exception for random string provider")
        void shouldThrowExceptionForRandomStringProvider() {
            assertThatThrownBy(() -> factory.createClient("random-provider-123"))
                    .isInstanceOf(IllegalArgumentException.class)
                    .hasMessage("Unsupported VCS provider: random-provider-123");
        }
    }
}

package org.rostilos.codecrow.core.model.vcs;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.NullSource;
import org.junit.jupiter.params.provider.ValueSource;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

@DisplayName("EVcsProvider")
class EVcsProviderTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        EVcsProvider[] values = EVcsProvider.values();
        
        assertThat(values).hasSize(4);
        assertThat(values).contains(
                EVcsProvider.BITBUCKET_CLOUD,
                EVcsProvider.BITBUCKET_SERVER,
                EVcsProvider.GITHUB,
                EVcsProvider.GITLAB
        );
    }

    @Test
    @DisplayName("getId should return lowercase id with dashes")
    void getIdShouldReturnLowercaseIdWithDashes() {
        assertThat(EVcsProvider.BITBUCKET_CLOUD.getId()).isEqualTo("bitbucket-cloud");
        assertThat(EVcsProvider.BITBUCKET_SERVER.getId()).isEqualTo("bitbucket-server");
        assertThat(EVcsProvider.GITHUB.getId()).isEqualTo("github");
        assertThat(EVcsProvider.GITLAB.getId()).isEqualTo("gitlab");
    }

    @Nested
    @DisplayName("fromId")
    class FromId {

        @ParameterizedTest
        @CsvSource({
                "bitbucket-cloud, BITBUCKET_CLOUD",
                "bitbucket_cloud, BITBUCKET_CLOUD",
                "BITBUCKET_CLOUD, BITBUCKET_CLOUD",
                "BITBUCKET-CLOUD, BITBUCKET_CLOUD",
                "bitbucket-server, BITBUCKET_SERVER",
                "github, GITHUB",
                "GITHUB, GITHUB",
                "gitlab, GITLAB",
                "GITLAB, GITLAB"
        })
        @DisplayName("should parse various formats correctly")
        void shouldParseVariousFormatsCorrectly(String input, String expected) {
            assertThat(EVcsProvider.fromId(input)).isEqualTo(EVcsProvider.valueOf(expected));
        }

        @ParameterizedTest
        @NullSource
        @DisplayName("should throw exception for null input")
        void shouldThrowExceptionForNullInput(String input) {
            assertThatThrownBy(() -> EVcsProvider.fromId(input))
                    .isInstanceOf(IllegalArgumentException.class)
                    .hasMessage("Provider ID cannot be null");
        }

        @ParameterizedTest
        @ValueSource(strings = {"unknown", "svn", "mercurial", "invalid"})
        @DisplayName("should throw exception for unknown provider")
        void shouldThrowExceptionForUnknownProvider(String input) {
            assertThatThrownBy(() -> EVcsProvider.fromId(input))
                    .isInstanceOf(IllegalArgumentException.class)
                    .hasMessageContaining("Unknown VCS provider");
        }
    }

    @Test
    @DisplayName("name should return string representation")
    void nameShouldReturnStringRepresentation() {
        assertThat(EVcsProvider.BITBUCKET_CLOUD.name()).isEqualTo("BITBUCKET_CLOUD");
        assertThat(EVcsProvider.GITHUB.name()).isEqualTo("GITHUB");
    }
}

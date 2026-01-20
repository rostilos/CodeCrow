package org.rostilos.codecrow.core.model.vcs;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("EVcsConnectionType")
class EVcsConnectionTypeTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        EVcsConnectionType[] values = EVcsConnectionType.values();
        
        assertThat(values).contains(
                EVcsConnectionType.OAUTH_MANUAL,
                EVcsConnectionType.APP,
                EVcsConnectionType.CONNECT_APP,
                EVcsConnectionType.GITHUB_APP,
                EVcsConnectionType.OAUTH_APP,
                EVcsConnectionType.PERSONAL_TOKEN,
                EVcsConnectionType.APPLICATION,
                EVcsConnectionType.REPOSITORY_TOKEN,
                EVcsConnectionType.ACCESS_TOKEN
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum for bitbucket types")
    void valueOfShouldReturnCorrectEnumForBitbucketTypes() {
        assertThat(EVcsConnectionType.valueOf("OAUTH_MANUAL")).isEqualTo(EVcsConnectionType.OAUTH_MANUAL);
        assertThat(EVcsConnectionType.valueOf("APP")).isEqualTo(EVcsConnectionType.APP);
        assertThat(EVcsConnectionType.valueOf("CONNECT_APP")).isEqualTo(EVcsConnectionType.CONNECT_APP);
    }

    @Test
    @DisplayName("valueOf should return correct enum for github types")
    void valueOfShouldReturnCorrectEnumForGithubTypes() {
        assertThat(EVcsConnectionType.valueOf("GITHUB_APP")).isEqualTo(EVcsConnectionType.GITHUB_APP);
        assertThat(EVcsConnectionType.valueOf("OAUTH_APP")).isEqualTo(EVcsConnectionType.OAUTH_APP);
    }

    @Test
    @DisplayName("valueOf should return correct enum for gitlab types")
    void valueOfShouldReturnCorrectEnumForGitlabTypes() {
        assertThat(EVcsConnectionType.valueOf("PERSONAL_TOKEN")).isEqualTo(EVcsConnectionType.PERSONAL_TOKEN);
        assertThat(EVcsConnectionType.valueOf("APPLICATION")).isEqualTo(EVcsConnectionType.APPLICATION);
    }

    @Test
    @DisplayName("name should return string representation")
    void nameShouldReturnStringRepresentation() {
        assertThat(EVcsConnectionType.GITHUB_APP.name()).isEqualTo("GITHUB_APP");
        assertThat(EVcsConnectionType.REPOSITORY_TOKEN.name()).isEqualTo("REPOSITORY_TOKEN");
    }
}

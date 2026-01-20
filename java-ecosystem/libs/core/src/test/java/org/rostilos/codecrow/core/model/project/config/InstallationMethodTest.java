package org.rostilos.codecrow.core.model.project.config;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("InstallationMethod")
class InstallationMethodTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        InstallationMethod[] values = InstallationMethod.values();
        
        assertThat(values).hasSize(3);
        assertThat(values).contains(
                InstallationMethod.WEBHOOK,
                InstallationMethod.PIPELINE,
                InstallationMethod.GITHUB_ACTION
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(InstallationMethod.valueOf("WEBHOOK")).isEqualTo(InstallationMethod.WEBHOOK);
        assertThat(InstallationMethod.valueOf("PIPELINE")).isEqualTo(InstallationMethod.PIPELINE);
        assertThat(InstallationMethod.valueOf("GITHUB_ACTION")).isEqualTo(InstallationMethod.GITHUB_ACTION);
    }

    @Test
    @DisplayName("name should return string representation")
    void nameShouldReturnStringRepresentation() {
        assertThat(InstallationMethod.WEBHOOK.name()).isEqualTo("WEBHOOK");
        assertThat(InstallationMethod.PIPELINE.name()).isEqualTo("PIPELINE");
    }
}

package org.rostilos.codecrow.core.model.vcs;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("EVcsSetupStatus")
class EVcsSetupStatusTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        EVcsSetupStatus[] values = EVcsSetupStatus.values();
        
        assertThat(values).hasSize(4);
        assertThat(values).contains(
                EVcsSetupStatus.CONNECTED,
                EVcsSetupStatus.PENDING,
                EVcsSetupStatus.ERROR,
                EVcsSetupStatus.DISABLED
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(EVcsSetupStatus.valueOf("CONNECTED")).isEqualTo(EVcsSetupStatus.CONNECTED);
        assertThat(EVcsSetupStatus.valueOf("PENDING")).isEqualTo(EVcsSetupStatus.PENDING);
        assertThat(EVcsSetupStatus.valueOf("ERROR")).isEqualTo(EVcsSetupStatus.ERROR);
        assertThat(EVcsSetupStatus.valueOf("DISABLED")).isEqualTo(EVcsSetupStatus.DISABLED);
    }

    @Test
    @DisplayName("name should return string representation")
    void nameShouldReturnStringRepresentation() {
        assertThat(EVcsSetupStatus.CONNECTED.name()).isEqualTo("CONNECTED");
        assertThat(EVcsSetupStatus.ERROR.name()).isEqualTo("ERROR");
    }
}

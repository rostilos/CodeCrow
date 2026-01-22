package org.rostilos.codecrow.core.model.workspace;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("EMembershipStatus")
class EMembershipStatusTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        EMembershipStatus[] values = EMembershipStatus.values();
        
        assertThat(values).hasSize(4);
        assertThat(values).contains(
                EMembershipStatus.PENDING,
                EMembershipStatus.ACTIVE,
                EMembershipStatus.REVOKED,
                EMembershipStatus.REJECTED
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(EMembershipStatus.valueOf("PENDING")).isEqualTo(EMembershipStatus.PENDING);
        assertThat(EMembershipStatus.valueOf("ACTIVE")).isEqualTo(EMembershipStatus.ACTIVE);
        assertThat(EMembershipStatus.valueOf("REVOKED")).isEqualTo(EMembershipStatus.REVOKED);
        assertThat(EMembershipStatus.valueOf("REJECTED")).isEqualTo(EMembershipStatus.REJECTED);
    }

    @Test
    @DisplayName("name should return string representation")
    void nameShouldReturnStringRepresentation() {
        assertThat(EMembershipStatus.PENDING.name()).isEqualTo("PENDING");
        assertThat(EMembershipStatus.ACTIVE.name()).isEqualTo("ACTIVE");
    }
}

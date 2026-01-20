package org.rostilos.codecrow.core.model.user;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("ERole")
class ERoleTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        ERole[] values = ERole.values();
        
        assertThat(values).hasSize(3);
        assertThat(values).contains(
                ERole.ROLE_USER,
                ERole.ROLE_MODERATOR,
                ERole.ROLE_ADMIN
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(ERole.valueOf("ROLE_USER")).isEqualTo(ERole.ROLE_USER);
        assertThat(ERole.valueOf("ROLE_MODERATOR")).isEqualTo(ERole.ROLE_MODERATOR);
        assertThat(ERole.valueOf("ROLE_ADMIN")).isEqualTo(ERole.ROLE_ADMIN);
    }

    @Test
    @DisplayName("name should return string representation")
    void nameShouldReturnStringRepresentation() {
        assertThat(ERole.ROLE_USER.name()).isEqualTo("ROLE_USER");
        assertThat(ERole.ROLE_ADMIN.name()).isEqualTo("ROLE_ADMIN");
    }
}

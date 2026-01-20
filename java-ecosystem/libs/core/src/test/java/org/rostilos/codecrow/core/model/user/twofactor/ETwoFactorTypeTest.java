package org.rostilos.codecrow.core.model.user.twofactor;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("ETwoFactorType")
class ETwoFactorTypeTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        ETwoFactorType[] values = ETwoFactorType.values();
        
        assertThat(values).hasSize(2);
        assertThat(values).contains(
                ETwoFactorType.TOTP,
                ETwoFactorType.EMAIL
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(ETwoFactorType.valueOf("TOTP")).isEqualTo(ETwoFactorType.TOTP);
        assertThat(ETwoFactorType.valueOf("EMAIL")).isEqualTo(ETwoFactorType.EMAIL);
    }

    @Test
    @DisplayName("name should return string representation")
    void nameShouldReturnStringRepresentation() {
        assertThat(ETwoFactorType.TOTP.name()).isEqualTo("TOTP");
        assertThat(ETwoFactorType.EMAIL.name()).isEqualTo("EMAIL");
    }
}

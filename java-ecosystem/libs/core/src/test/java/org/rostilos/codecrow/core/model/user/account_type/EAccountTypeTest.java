package org.rostilos.codecrow.core.model.user.account_type;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("EAccountType")
class EAccountTypeTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        EAccountType[] values = EAccountType.values();
        
        assertThat(values).hasSize(4);
        assertThat(values).contains(
                EAccountType.TYPE_DEFAULT,
                EAccountType.TYPE_PRO,
                EAccountType.TYPE_ENTERPRISE,
                EAccountType.TYPE_ADMIN
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(EAccountType.valueOf("TYPE_DEFAULT")).isEqualTo(EAccountType.TYPE_DEFAULT);
        assertThat(EAccountType.valueOf("TYPE_PRO")).isEqualTo(EAccountType.TYPE_PRO);
        assertThat(EAccountType.valueOf("TYPE_ENTERPRISE")).isEqualTo(EAccountType.TYPE_ENTERPRISE);
        assertThat(EAccountType.valueOf("TYPE_ADMIN")).isEqualTo(EAccountType.TYPE_ADMIN);
    }

    @Test
    @DisplayName("name should return string representation")
    void nameShouldReturnStringRepresentation() {
        assertThat(EAccountType.TYPE_DEFAULT.name()).isEqualTo("TYPE_DEFAULT");
        assertThat(EAccountType.TYPE_ENTERPRISE.name()).isEqualTo("TYPE_ENTERPRISE");
    }
}

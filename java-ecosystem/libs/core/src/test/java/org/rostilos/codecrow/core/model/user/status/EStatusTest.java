package org.rostilos.codecrow.core.model.user.status;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("EStatus")
class EStatusTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        EStatus[] values = EStatus.values();
        
        assertThat(values).hasSize(3);
        assertThat(values).contains(
                EStatus.STATUS_ACTIVE,
                EStatus.STATUS_DISABLED,
                EStatus.STATUS_BANNED
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(EStatus.valueOf("STATUS_ACTIVE")).isEqualTo(EStatus.STATUS_ACTIVE);
        assertThat(EStatus.valueOf("STATUS_DISABLED")).isEqualTo(EStatus.STATUS_DISABLED);
        assertThat(EStatus.valueOf("STATUS_BANNED")).isEqualTo(EStatus.STATUS_BANNED);
    }

    @Test
    @DisplayName("name should return string representation")
    void nameShouldReturnStringRepresentation() {
        assertThat(EStatus.STATUS_ACTIVE.name()).isEqualTo("STATUS_ACTIVE");
        assertThat(EStatus.STATUS_BANNED.name()).isEqualTo("STATUS_BANNED");
    }
}

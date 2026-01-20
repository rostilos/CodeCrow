package org.rostilos.codecrow.core.model.rag;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("DeltaIndexStatus")
class DeltaIndexStatusTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        DeltaIndexStatus[] values = DeltaIndexStatus.values();
        
        assertThat(values).hasSize(5);
        assertThat(values).contains(
                DeltaIndexStatus.CREATING,
                DeltaIndexStatus.READY,
                DeltaIndexStatus.STALE,
                DeltaIndexStatus.ARCHIVED,
                DeltaIndexStatus.FAILED
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(DeltaIndexStatus.valueOf("CREATING")).isEqualTo(DeltaIndexStatus.CREATING);
        assertThat(DeltaIndexStatus.valueOf("READY")).isEqualTo(DeltaIndexStatus.READY);
        assertThat(DeltaIndexStatus.valueOf("STALE")).isEqualTo(DeltaIndexStatus.STALE);
        assertThat(DeltaIndexStatus.valueOf("ARCHIVED")).isEqualTo(DeltaIndexStatus.ARCHIVED);
        assertThat(DeltaIndexStatus.valueOf("FAILED")).isEqualTo(DeltaIndexStatus.FAILED);
    }

    @Test
    @DisplayName("name should return string representation")
    void nameShouldReturnStringRepresentation() {
        assertThat(DeltaIndexStatus.CREATING.name()).isEqualTo("CREATING");
        assertThat(DeltaIndexStatus.READY.name()).isEqualTo("READY");
    }
}

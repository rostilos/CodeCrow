package org.rostilos.codecrow.core.model.analysis;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("RagIndexingStatus")
class RagIndexingStatusTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        RagIndexingStatus[] values = RagIndexingStatus.values();
        
        assertThat(values).hasSize(5);
        assertThat(values).contains(
                RagIndexingStatus.NOT_INDEXED,
                RagIndexingStatus.INDEXING,
                RagIndexingStatus.INDEXED,
                RagIndexingStatus.UPDATING,
                RagIndexingStatus.FAILED
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(RagIndexingStatus.valueOf("NOT_INDEXED")).isEqualTo(RagIndexingStatus.NOT_INDEXED);
        assertThat(RagIndexingStatus.valueOf("INDEXING")).isEqualTo(RagIndexingStatus.INDEXING);
        assertThat(RagIndexingStatus.valueOf("INDEXED")).isEqualTo(RagIndexingStatus.INDEXED);
        assertThat(RagIndexingStatus.valueOf("UPDATING")).isEqualTo(RagIndexingStatus.UPDATING);
        assertThat(RagIndexingStatus.valueOf("FAILED")).isEqualTo(RagIndexingStatus.FAILED);
    }

    @Test
    @DisplayName("ordinal values should be in correct order")
    void ordinalValuesShouldBeInCorrectOrder() {
        assertThat(RagIndexingStatus.NOT_INDEXED.ordinal()).isEqualTo(0);
        assertThat(RagIndexingStatus.INDEXING.ordinal()).isEqualTo(1);
        assertThat(RagIndexingStatus.INDEXED.ordinal()).isEqualTo(2);
        assertThat(RagIndexingStatus.UPDATING.ordinal()).isEqualTo(3);
        assertThat(RagIndexingStatus.FAILED.ordinal()).isEqualTo(4);
    }
}

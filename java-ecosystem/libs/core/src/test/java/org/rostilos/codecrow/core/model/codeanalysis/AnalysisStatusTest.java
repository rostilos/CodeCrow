package org.rostilos.codecrow.core.model.codeanalysis;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("AnalysisStatus")
class AnalysisStatusTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        AnalysisStatus[] values = AnalysisStatus.values();
        
        assertThat(values).hasSize(4);
        assertThat(values).contains(
                AnalysisStatus.ACCEPTED,
                AnalysisStatus.REJECTED,
                AnalysisStatus.PENDING,
                AnalysisStatus.ERROR
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(AnalysisStatus.valueOf("ACCEPTED")).isEqualTo(AnalysisStatus.ACCEPTED);
        assertThat(AnalysisStatus.valueOf("PENDING")).isEqualTo(AnalysisStatus.PENDING);
        assertThat(AnalysisStatus.valueOf("ERROR")).isEqualTo(AnalysisStatus.ERROR);
    }

    @Test
    @DisplayName("name should return string representation")
    void nameShouldReturnStringRepresentation() {
        assertThat(AnalysisStatus.ACCEPTED.name()).isEqualTo("ACCEPTED");
        assertThat(AnalysisStatus.REJECTED.name()).isEqualTo("REJECTED");
    }
}

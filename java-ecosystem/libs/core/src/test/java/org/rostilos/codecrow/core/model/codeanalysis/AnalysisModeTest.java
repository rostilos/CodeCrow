package org.rostilos.codecrow.core.model.codeanalysis;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("AnalysisMode")
class AnalysisModeTest {

    @Test
    @DisplayName("should have FULL and INCREMENTAL values")
    void shouldHaveFullAndIncrementalValues() {
        AnalysisMode[] values = AnalysisMode.values();
        
        assertThat(values).hasSize(2);
        assertThat(values).contains(AnalysisMode.FULL, AnalysisMode.INCREMENTAL);
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(AnalysisMode.valueOf("FULL")).isEqualTo(AnalysisMode.FULL);
        assertThat(AnalysisMode.valueOf("INCREMENTAL")).isEqualTo(AnalysisMode.INCREMENTAL);
    }

    @Test
    @DisplayName("name should return string representation")
    void nameShouldReturnStringRepresentation() {
        assertThat(AnalysisMode.FULL.name()).isEqualTo("FULL");
        assertThat(AnalysisMode.INCREMENTAL.name()).isEqualTo("INCREMENTAL");
    }

    @Test
    @DisplayName("ordinal should return correct position")
    void ordinalShouldReturnCorrectPosition() {
        assertThat(AnalysisMode.FULL.ordinal()).isEqualTo(0);
        assertThat(AnalysisMode.INCREMENTAL.ordinal()).isEqualTo(1);
    }
}

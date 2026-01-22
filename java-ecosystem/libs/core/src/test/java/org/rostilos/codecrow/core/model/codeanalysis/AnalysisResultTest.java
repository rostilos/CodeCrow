package org.rostilos.codecrow.core.model.codeanalysis;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("AnalysisResult")
class AnalysisResultTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        AnalysisResult[] values = AnalysisResult.values();
        
        assertThat(values).hasSize(3);
        assertThat(values).contains(
                AnalysisResult.PASSED,
                AnalysisResult.FAILED,
                AnalysisResult.SKIPPED
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(AnalysisResult.valueOf("PASSED")).isEqualTo(AnalysisResult.PASSED);
        assertThat(AnalysisResult.valueOf("FAILED")).isEqualTo(AnalysisResult.FAILED);
        assertThat(AnalysisResult.valueOf("SKIPPED")).isEqualTo(AnalysisResult.SKIPPED);
    }

    @Test
    @DisplayName("name should return string representation")
    void nameShouldReturnStringRepresentation() {
        assertThat(AnalysisResult.PASSED.name()).isEqualTo("PASSED");
        assertThat(AnalysisResult.FAILED.name()).isEqualTo("FAILED");
        assertThat(AnalysisResult.SKIPPED.name()).isEqualTo("SKIPPED");
    }
}

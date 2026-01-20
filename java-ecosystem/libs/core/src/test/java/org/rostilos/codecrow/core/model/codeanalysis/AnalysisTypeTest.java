package org.rostilos.codecrow.core.model.codeanalysis;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("AnalysisType")
class AnalysisTypeTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        AnalysisType[] values = AnalysisType.values();
        
        assertThat(values).hasSize(5);
        assertThat(values).contains(
                AnalysisType.PR_REVIEW,
                AnalysisType.COMMIT_ANALYSIS,
                AnalysisType.BRANCH_ANALYSIS,
                AnalysisType.SECURITY_SCAN,
                AnalysisType.QUALITY_CHECK
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(AnalysisType.valueOf("PR_REVIEW")).isEqualTo(AnalysisType.PR_REVIEW);
        assertThat(AnalysisType.valueOf("BRANCH_ANALYSIS")).isEqualTo(AnalysisType.BRANCH_ANALYSIS);
    }

    @Test
    @DisplayName("name should return string representation")
    void nameShouldReturnStringRepresentation() {
        assertThat(AnalysisType.PR_REVIEW.name()).isEqualTo("PR_REVIEW");
        assertThat(AnalysisType.SECURITY_SCAN.name()).isEqualTo("SECURITY_SCAN");
    }
}

package org.rostilos.codecrow.core.model.analysis;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("AnalysisLockType")
class AnalysisLockTypeTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        AnalysisLockType[] values = AnalysisLockType.values();
        
        assertThat(values).hasSize(3);
        assertThat(values).contains(
                AnalysisLockType.PR_ANALYSIS,
                AnalysisLockType.BRANCH_ANALYSIS,
                AnalysisLockType.RAG_INDEXING
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(AnalysisLockType.valueOf("PR_ANALYSIS")).isEqualTo(AnalysisLockType.PR_ANALYSIS);
        assertThat(AnalysisLockType.valueOf("BRANCH_ANALYSIS")).isEqualTo(AnalysisLockType.BRANCH_ANALYSIS);
        assertThat(AnalysisLockType.valueOf("RAG_INDEXING")).isEqualTo(AnalysisLockType.RAG_INDEXING);
    }

    @Test
    @DisplayName("ordinal values should be in correct order")
    void ordinalValuesShouldBeInCorrectOrder() {
        assertThat(AnalysisLockType.PR_ANALYSIS.ordinal()).isEqualTo(0);
        assertThat(AnalysisLockType.BRANCH_ANALYSIS.ordinal()).isEqualTo(1);
        assertThat(AnalysisLockType.RAG_INDEXING.ordinal()).isEqualTo(2);
    }
}

package org.rostilos.codecrow.analysisengine.exception;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("Exception classes")
class ExceptionTest {

    @Nested
    @DisplayName("DiffTooLargeException")
    class DiffTooLargeTests {

        @Test void fieldsAreSet() {
            DiffTooLargeException ex = new DiffTooLargeException(50_000, 30_000, 1L, 42L);
            assertThat(ex.getEstimatedTokens()).isEqualTo(50_000);
            assertThat(ex.getMaxAllowedTokens()).isEqualTo(30_000);
            assertThat(ex.getProjectId()).isEqualTo(1L);
            assertThat(ex.getPullRequestId()).isEqualTo(42L);
        }

        @Test void messageIsFormatted() {
            DiffTooLargeException ex = new DiffTooLargeException(50_000, 30_000, 1L, 42L);
            assertThat(ex.getMessage())
                    .contains("50000")
                    .contains("30000")
                    .contains("project=1")
                    .contains("PR=42");
        }

        @Test void utilizationPercentage_normal() {
            DiffTooLargeException ex = new DiffTooLargeException(150, 100, 1L, 1L);
            assertThat(ex.getUtilizationPercentage()).isEqualTo(150.0);
        }

        @Test void utilizationPercentage_zeroMax() {
            DiffTooLargeException ex = new DiffTooLargeException(100, 0, 1L, 1L);
            assertThat(ex.getUtilizationPercentage()).isEqualTo(0.0);
        }
    }

    @Nested
    @DisplayName("AnalysisLockedException")
    class AnalysisLockedTests {

        @Test void fieldsAreSet() {
            AnalysisLockedException ex = new AnalysisLockedException("BRANCH_ANALYSIS", "main", 5L);
            assertThat(ex.getLockType()).isEqualTo("BRANCH_ANALYSIS");
            assertThat(ex.getBranchName()).isEqualTo("main");
            assertThat(ex.getProjectId()).isEqualTo(5L);
        }

        @Test void messageContainsDetails() {
            AnalysisLockedException ex = new AnalysisLockedException("PR_ANALYSIS", "feature", 10L);
            assertThat(ex.getMessage())
                    .contains("project=10")
                    .contains("branch=feature")
                    .contains("type=PR_ANALYSIS");
        }
    }
}

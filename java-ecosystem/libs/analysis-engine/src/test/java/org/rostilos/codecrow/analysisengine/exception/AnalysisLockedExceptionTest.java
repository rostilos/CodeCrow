package org.rostilos.codecrow.analysisengine.exception;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("AnalysisLockedException")
class AnalysisLockedExceptionTest {

    @Nested
    @DisplayName("Constructor")
    class ConstructorTests {

        @Test
        @DisplayName("should create exception with all fields")
        void shouldCreateExceptionWithAllFields() {
            AnalysisLockedException exception = new AnalysisLockedException(
                    "PR_ANALYSIS",
                    "feature/new-feature",
                    42L
            );
            
            assertThat(exception.getLockType()).isEqualTo("PR_ANALYSIS");
            assertThat(exception.getBranchName()).isEqualTo("feature/new-feature");
            assertThat(exception.getProjectId()).isEqualTo(42L);
        }

        @Test
        @DisplayName("should generate proper message")
        void shouldGenerateProperMessage() {
            AnalysisLockedException exception = new AnalysisLockedException(
                    "BRANCH_ANALYSIS",
                    "main",
                    100L
            );
            
            assertThat(exception.getMessage())
                    .contains("project=100")
                    .contains("branch=main")
                    .contains("type=BRANCH_ANALYSIS");
        }
    }

    @Nested
    @DisplayName("Getters")
    class GetterTests {

        @Test
        @DisplayName("getLockType() should return lock type")
        void getLockTypeShouldReturnLockType() {
            AnalysisLockedException exception = new AnalysisLockedException("PR", "branch", 1L);
            assertThat(exception.getLockType()).isEqualTo("PR");
        }

        @Test
        @DisplayName("getBranchName() should return branch name")
        void getBranchNameShouldReturnBranchName() {
            AnalysisLockedException exception = new AnalysisLockedException("PR", "develop", 1L);
            assertThat(exception.getBranchName()).isEqualTo("develop");
        }

        @Test
        @DisplayName("getProjectId() should return project ID")
        void getProjectIdShouldReturnProjectId() {
            AnalysisLockedException exception = new AnalysisLockedException("PR", "branch", 999L);
            assertThat(exception.getProjectId()).isEqualTo(999L);
        }
    }

    @Nested
    @DisplayName("Inheritance")
    class InheritanceTests {

        @Test
        @DisplayName("should be a RuntimeException")
        void shouldBeARuntimeException() {
            AnalysisLockedException exception = new AnalysisLockedException("PR", "branch", 1L);
            assertThat(exception).isInstanceOf(RuntimeException.class);
        }

        @Test
        @DisplayName("should be throwable without declaration")
        void shouldBeThrowableWithoutDeclaration() {
            try {
                throw new AnalysisLockedException("TEST", "test-branch", 1L);
            } catch (AnalysisLockedException e) {
                assertThat(e.getLockType()).isEqualTo("TEST");
            }
        }
    }
}

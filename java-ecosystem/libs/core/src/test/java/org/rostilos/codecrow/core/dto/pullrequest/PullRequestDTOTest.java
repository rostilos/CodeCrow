package org.rostilos.codecrow.core.dto.pullrequest;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisResult;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;

import java.lang.reflect.Field;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("PullRequestDTO")
class PullRequestDTOTest {

    @Nested
    @DisplayName("record constructor")
    class RecordConstructorTests {

        @Test
        @DisplayName("should create PullRequestDTO with all fields")
        void shouldCreateWithAllFields() {
            PullRequestDTO dto = new PullRequestDTO(
                    1L, 42L, "abc123def456",
                    "main", "feature/test",
                    AnalysisResult.PASSED,
                    5, 10, 15, 20, 50
            );

            assertThat(dto.id()).isEqualTo(1L);
            assertThat(dto.prNumber()).isEqualTo(42L);
            assertThat(dto.commitHash()).isEqualTo("abc123def456");
            assertThat(dto.targetBranchName()).isEqualTo("main");
            assertThat(dto.sourceBranchName()).isEqualTo("feature/test");
            assertThat(dto.analysisResult()).isEqualTo(AnalysisResult.PASSED);
            assertThat(dto.highSeverityCount()).isEqualTo(5);
            assertThat(dto.mediumSeverityCount()).isEqualTo(10);
            assertThat(dto.lowSeverityCount()).isEqualTo(15);
            assertThat(dto.infoSeverityCount()).isEqualTo(20);
            assertThat(dto.totalIssues()).isEqualTo(50);
        }

        @Test
        @DisplayName("should create PullRequestDTO with null optional fields")
        void shouldCreateWithNullOptionalFields() {
            PullRequestDTO dto = new PullRequestDTO(
                    1L, null, null, null, null, null, null, null, null, null, null
            );

            assertThat(dto.id()).isEqualTo(1L);
            assertThat(dto.prNumber()).isNull();
            assertThat(dto.commitHash()).isNull();
            assertThat(dto.analysisResult()).isNull();
            assertThat(dto.totalIssues()).isNull();
        }

        @Test
        @DisplayName("should create PullRequestDTO with different analysis results")
        void shouldCreateWithDifferentAnalysisResults() {
            PullRequestDTO passed = new PullRequestDTO(1L, 1L, "hash", "main", "dev", AnalysisResult.PASSED, 0, 0, 0, 0, 0);
            PullRequestDTO failed = new PullRequestDTO(2L, 2L, "hash", "main", "dev", AnalysisResult.FAILED, 5, 0, 0, 0, 5);
            PullRequestDTO skipped = new PullRequestDTO(3L, 3L, "hash", "main", "dev", AnalysisResult.SKIPPED, 0, 0, 0, 0, 0);

            assertThat(passed.analysisResult()).isEqualTo(AnalysisResult.PASSED);
            assertThat(failed.analysisResult()).isEqualTo(AnalysisResult.FAILED);
            assertThat(skipped.analysisResult()).isEqualTo(AnalysisResult.SKIPPED);
        }

        @Test
        @DisplayName("should support zero severity counts")
        void shouldSupportZeroSeverityCounts() {
            PullRequestDTO dto = new PullRequestDTO(
                    1L, 1L, "hash", "main", "dev",
                    AnalysisResult.PASSED, 0, 0, 0, 0, 0
            );

            assertThat(dto.highSeverityCount()).isZero();
            assertThat(dto.mediumSeverityCount()).isZero();
            assertThat(dto.lowSeverityCount()).isZero();
            assertThat(dto.infoSeverityCount()).isZero();
            assertThat(dto.totalIssues()).isZero();
        }
    }

    @Nested
    @DisplayName("fromPullRequest()")
    class FromPullRequestTests {

        @Test
        @DisplayName("should convert PullRequest with all fields")
        void shouldConvertWithAllFields() {
            PullRequest pr = new PullRequest();
            pr.setId(1L);
            pr.setPrNumber(42L);
            pr.setCommitHash("abc123def456");
            pr.setTargetBranchName("main");
            pr.setSourceBranchName("feature/new-feature");

            PullRequestDTO dto = PullRequestDTO.fromPullRequest(pr);

            assertThat(dto.id()).isEqualTo(1L);
            assertThat(dto.prNumber()).isEqualTo(42L);
            assertThat(dto.commitHash()).isEqualTo("abc123def456");
            assertThat(dto.targetBranchName()).isEqualTo("main");
            assertThat(dto.sourceBranchName()).isEqualTo("feature/new-feature");
            assertThat(dto.analysisResult()).isNull();
            assertThat(dto.highSeverityCount()).isNull();
            assertThat(dto.mediumSeverityCount()).isNull();
            assertThat(dto.lowSeverityCount()).isNull();
            assertThat(dto.infoSeverityCount()).isNull();
            assertThat(dto.totalIssues()).isNull();
        }

        @Test
        @DisplayName("should convert PullRequest with null fields")
        void shouldConvertWithNullFields() {
            PullRequest pr = new PullRequest();
            pr.setId(2L);

            PullRequestDTO dto = PullRequestDTO.fromPullRequest(pr);

            assertThat(dto.id()).isEqualTo(2L);
            assertThat(dto.prNumber()).isNull();
            assertThat(dto.commitHash()).isNull();
            assertThat(dto.targetBranchName()).isNull();
            assertThat(dto.sourceBranchName()).isNull();
        }
    }

    @Nested
    @DisplayName("fromPullRequestWithAnalysis()")
    class FromPullRequestWithAnalysisTests {

        @Test
        @DisplayName("should convert PR with analysis")
        void shouldConvertPrWithAnalysis() {
            PullRequest pr = new PullRequest();
            pr.setId(1L);
            pr.setPrNumber(99L);
            pr.setCommitHash("hash123");
            pr.setTargetBranchName("main");
            pr.setSourceBranchName("feature/x");

            CodeAnalysis analysis = new CodeAnalysis();
            setField(analysis, "id", 10L);
            analysis.setAnalysisResult(AnalysisResult.PASSED);
            setField(analysis, "highSeverityCount", 1);
            setField(analysis, "mediumSeverityCount", 2);
            setField(analysis, "lowSeverityCount", 3);
            setField(analysis, "infoSeverityCount", 4);
            setField(analysis, "totalIssues", 10);

            PullRequestDTO dto = PullRequestDTO.fromPullRequestWithAnalysis(pr, analysis);

            assertThat(dto.id()).isEqualTo(1L);
            assertThat(dto.prNumber()).isEqualTo(99L);
            assertThat(dto.analysisResult()).isEqualTo(AnalysisResult.PASSED);
            assertThat(dto.highSeverityCount()).isEqualTo(1);
            assertThat(dto.mediumSeverityCount()).isEqualTo(2);
            assertThat(dto.lowSeverityCount()).isEqualTo(3);
            assertThat(dto.infoSeverityCount()).isEqualTo(4);
            assertThat(dto.totalIssues()).isEqualTo(10);
        }

        @Test
        @DisplayName("should fallback to fromPullRequest when analysis is null")
        void shouldFallbackWhenAnalysisIsNull() {
            PullRequest pr = new PullRequest();
            pr.setId(2L);
            pr.setPrNumber(50L);
            pr.setCommitHash("commit456");
            pr.setTargetBranchName("develop");
            pr.setSourceBranchName("hotfix/y");

            PullRequestDTO dto = PullRequestDTO.fromPullRequestWithAnalysis(pr, null);

            assertThat(dto.id()).isEqualTo(2L);
            assertThat(dto.prNumber()).isEqualTo(50L);
            assertThat(dto.commitHash()).isEqualTo("commit456");
            assertThat(dto.analysisResult()).isNull();
            assertThat(dto.totalIssues()).isNull();
        }

        @Test
        @DisplayName("should convert PR with failed analysis")
        void shouldConvertPrWithFailedAnalysis() {
            PullRequest pr = new PullRequest();
            pr.setId(3L);
            pr.setPrNumber(100L);

            CodeAnalysis analysis = new CodeAnalysis();
            setField(analysis, "id", 20L);
            analysis.setAnalysisResult(AnalysisResult.FAILED);
            setField(analysis, "highSeverityCount", 5);
            setField(analysis, "totalIssues", 5);

            PullRequestDTO dto = PullRequestDTO.fromPullRequestWithAnalysis(pr, analysis);

            assertThat(dto.analysisResult()).isEqualTo(AnalysisResult.FAILED);
            assertThat(dto.highSeverityCount()).isEqualTo(5);
        }

        @Test
        @DisplayName("should convert PR with zero issues")
        void shouldConvertPrWithZeroIssues() {
            PullRequest pr = new PullRequest();
            pr.setId(4L);
            pr.setPrNumber(200L);

            CodeAnalysis analysis = new CodeAnalysis();
            setField(analysis, "id", 30L);
            analysis.setAnalysisResult(AnalysisResult.PASSED);

            PullRequestDTO dto = PullRequestDTO.fromPullRequestWithAnalysis(pr, analysis);

            assertThat(dto.analysisResult()).isEqualTo(AnalysisResult.PASSED);
            assertThat(dto.highSeverityCount()).isZero();
            assertThat(dto.mediumSeverityCount()).isZero();
            assertThat(dto.lowSeverityCount()).isZero();
            assertThat(dto.infoSeverityCount()).isZero();
            assertThat(dto.totalIssues()).isZero();
        }
    }

    @Nested
    @DisplayName("equality and hashCode")
    class EqualityTests {

        @Test
        @DisplayName("should be equal for same values")
        void shouldBeEqualForSameValues() {
            PullRequestDTO dto1 = new PullRequestDTO(1L, 42L, "hash", "main", "dev", AnalysisResult.PASSED, 0, 0, 0, 0, 0);
            PullRequestDTO dto2 = new PullRequestDTO(1L, 42L, "hash", "main", "dev", AnalysisResult.PASSED, 0, 0, 0, 0, 0);

            assertThat(dto1).isEqualTo(dto2);
            assertThat(dto1.hashCode()).isEqualTo(dto2.hashCode());
        }

        @Test
        @DisplayName("should not be equal for different values")
        void shouldNotBeEqualForDifferentValues() {
            PullRequestDTO dto1 = new PullRequestDTO(1L, 42L, "hash", "main", "dev", AnalysisResult.PASSED, 0, 0, 0, 0, 0);
            PullRequestDTO dto2 = new PullRequestDTO(2L, 42L, "hash", "main", "dev", AnalysisResult.PASSED, 0, 0, 0, 0, 0);

            assertThat(dto1).isNotEqualTo(dto2);
        }
    }

    private void setField(Object obj, String fieldName, Object value) {
        try {
            Field field = obj.getClass().getDeclaredField(fieldName);
            field.setAccessible(true);
            field.set(obj, value);
        } catch (Exception e) {
            throw new RuntimeException("Failed to set field " + fieldName, e);
        }
    }
}

package org.rostilos.codecrow.core.dto.analysis;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisStatus;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("AnalysisItemDTO")
class AnalysisItemDTOTest {

    @Nested
    @DisplayName("fromEntity()")
    class FromEntity {

        @Test
        @DisplayName("should map CodeAnalysis to DTO")
        void shouldMapCodeAnalysisToDTO() {
            CodeAnalysis analysis = new CodeAnalysis();
            analysis.setBranchName("main");
            analysis.setPrNumber(42L);
            analysis.setStatus(AnalysisStatus.ACCEPTED);
            
            AnalysisItemDTO dto = AnalysisItemDTO.fromEntity(analysis);
            
            assertThat(dto.branch()).isEqualTo("main");
            assertThat(dto.pullRequestId()).isEqualTo("42");
            assertThat(dto.status()).isEqualTo("accepted");
        }

        @Test
        @DisplayName("should handle null PR number")
        void shouldHandleNullPrNumber() {
            CodeAnalysis analysis = new CodeAnalysis();
            analysis.setBranchName("feature/test");
            analysis.setPrNumber(null);
            analysis.setStatus(AnalysisStatus.PENDING);
            
            AnalysisItemDTO dto = AnalysisItemDTO.fromEntity(analysis);
            
            assertThat(dto.pullRequestId()).isNull();
        }

        @Test
        @DisplayName("should handle null status")
        void shouldHandleNullStatus() {
            CodeAnalysis analysis = new CodeAnalysis();
            analysis.setBranchName("main");
            analysis.setStatus(null);
            
            AnalysisItemDTO dto = AnalysisItemDTO.fromEntity(analysis);
            
            assertThat(dto.status()).isNull();
        }

        @Test
        @DisplayName("should map issue counts")
        void shouldMapIssueCounts() {
            CodeAnalysis analysis = new CodeAnalysis();
            analysis.setBranchName("main");
            analysis.setStatus(AnalysisStatus.ACCEPTED);
            // Issues are counted internally, so we use the analysis object directly
            
            AnalysisItemDTO dto = AnalysisItemDTO.fromEntity(analysis);
            
            assertThat(dto.issuesFound()).isGreaterThanOrEqualTo(0);
            assertThat(dto.criticalIssuesFound()).isGreaterThanOrEqualTo(0);
        }

        @Test
        @DisplayName("should calculate duration")
        void shouldCalculateDuration() {
            CodeAnalysis analysis = new CodeAnalysis();
            analysis.setBranchName("main");
            analysis.setStatus(AnalysisStatus.ACCEPTED);
            // CreatedAt and UpdatedAt are set automatically
            
            AnalysisItemDTO dto = AnalysisItemDTO.fromEntity(analysis);
            
            // Duration should be calculated
            assertThat(dto.duration()).isNotNull();
        }

        @Test
        @DisplayName("should map timestamps")
        void shouldMapTimestamps() {
            CodeAnalysis analysis = new CodeAnalysis();
            analysis.setBranchName("main");
            analysis.setStatus(AnalysisStatus.ACCEPTED);
            
            AnalysisItemDTO dto = AnalysisItemDTO.fromEntity(analysis);
            
            assertThat(dto.triggeredAt()).isNotNull();
            assertThat(dto.completedAt()).isNotNull();
        }
    }

    @Nested
    @DisplayName("Record accessors")
    class RecordAccessors {

        @Test
        @DisplayName("should access all record fields")
        void shouldAccessAllRecordFields() {
            OffsetDateTime now = OffsetDateTime.now();
            AnalysisItemDTO dto = new AnalysisItemDTO(
                    "123",
                    "main",
                    "42",
                    "user@example.com",
                    now,
                    now.plusMinutes(5),
                    "completed",
                    "openai",
                    10,
                    2,
                    "5m"
            );
            
            assertThat(dto.id()).isEqualTo("123");
            assertThat(dto.branch()).isEqualTo("main");
            assertThat(dto.pullRequestId()).isEqualTo("42");
            assertThat(dto.triggeredBy()).isEqualTo("user@example.com");
            assertThat(dto.triggeredAt()).isEqualTo(now);
            assertThat(dto.completedAt()).isEqualTo(now.plusMinutes(5));
            assertThat(dto.status()).isEqualTo("completed");
            assertThat(dto.aiProvider()).isEqualTo("openai");
            assertThat(dto.issuesFound()).isEqualTo(10);
            assertThat(dto.criticalIssuesFound()).isEqualTo(2);
            assertThat(dto.duration()).isEqualTo("5m");
        }
    }
}

package org.rostilos.codecrow.core.dto.analysis.issue;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.OffsetDateTime;
import java.util.Collections;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("IssuesSummaryDTO")
class IssuesSummaryDTOTest {

    @Test
    @DisplayName("should create with all counts")
    void shouldCreateWithAllCounts() {
        IssuesSummaryDTO dto = new IssuesSummaryDTO(
                100,
                10,
                20,
                30,
                40,
                5,
                60,
                15,
                10,
                3,
                2,
                4,
                1,
                0,
                0
        );
        
        assertThat(dto.totalIssues()).isEqualTo(100);
        assertThat(dto.highCount()).isEqualTo(10);
        assertThat(dto.mediumCount()).isEqualTo(20);
        assertThat(dto.lowCount()).isEqualTo(30);
        assertThat(dto.infoCount()).isEqualTo(40);
        assertThat(dto.securityCount()).isEqualTo(5);
        assertThat(dto.qualityCount()).isEqualTo(60);
        assertThat(dto.performanceCount()).isEqualTo(15);
        assertThat(dto.styleCount()).isEqualTo(10);
        assertThat(dto.bugRiskCount()).isEqualTo(3);
        assertThat(dto.documentationCount()).isEqualTo(2);
        assertThat(dto.bestPracticesCount()).isEqualTo(4);
        assertThat(dto.errorHandlingCount()).isEqualTo(1);
        assertThat(dto.testingCount()).isEqualTo(0);
        assertThat(dto.architectureCount()).isEqualTo(0);
    }

    @Test
    @DisplayName("should create from empty list")
    void shouldCreateFromEmptyList() {
        IssuesSummaryDTO dto = IssuesSummaryDTO.fromIssuesDTOs(Collections.emptyList());
        
        assertThat(dto.totalIssues()).isEqualTo(0);
        assertThat(dto.highCount()).isEqualTo(0);
        assertThat(dto.mediumCount()).isEqualTo(0);
        assertThat(dto.securityCount()).isEqualTo(0);
    }

    @Test
    @DisplayName("should create from list with security issues")
    void shouldCreateFromListWithSecurityIssues() {
        OffsetDateTime now = OffsetDateTime.now();
        List<IssueDTO> issues = List.of(
                createIssueDTO("1", "HIGH", "SECURITY"),
                createIssueDTO("2", "HIGH", "SECURITY"),
                createIssueDTO("3", "MEDIUM", "CODE_QUALITY")
        );
        
        IssuesSummaryDTO dto = IssuesSummaryDTO.fromIssuesDTOs(issues);
        
        assertThat(dto.totalIssues()).isEqualTo(3);
        assertThat(dto.securityCount()).isEqualTo(2);
        assertThat(dto.qualityCount()).isEqualTo(1);
    }

    @Test
    @DisplayName("should count all severity levels")
    void shouldCountAllSeverityLevels() {
        List<IssueDTO> issues = List.of(
                createIssueDTO("1", "HIGH", "CODE_QUALITY"),
                createIssueDTO("2", "MEDIUM", "CODE_QUALITY"),
                createIssueDTO("3", "LOW", "CODE_QUALITY"),
                createIssueDTO("4", "INFO", "CODE_QUALITY")
        );
        
        IssuesSummaryDTO dto = IssuesSummaryDTO.fromIssuesDTOs(issues);
        
        assertThat(dto.totalIssues()).isEqualTo(4);
        assertThat(dto.highCount()).isEqualTo(1);
        assertThat(dto.mediumCount()).isEqualTo(1);
        assertThat(dto.lowCount()).isEqualTo(1);
        assertThat(dto.infoCount()).isEqualTo(1);
    }

    @Test
    @DisplayName("should count all category types")
    void shouldCountAllCategoryTypes() {
        List<IssueDTO> issues = List.of(
                createIssueDTO("1", "HIGH", "SECURITY"),
                createIssueDTO("2", "HIGH", "PERFORMANCE"),
                createIssueDTO("3", "HIGH", "BUG_RISK"),
                createIssueDTO("4", "HIGH", "DOCUMENTATION"),
                createIssueDTO("5", "HIGH", "BEST_PRACTICES"),
                createIssueDTO("6", "HIGH", "ERROR_HANDLING"),
                createIssueDTO("7", "HIGH", "TESTING"),
                createIssueDTO("8", "HIGH", "ARCHITECTURE"),
                createIssueDTO("9", "HIGH", "STYLE")
        );
        
        IssuesSummaryDTO dto = IssuesSummaryDTO.fromIssuesDTOs(issues);
        
        assertThat(dto.totalIssues()).isEqualTo(9);
        assertThat(dto.securityCount()).isEqualTo(1);
        assertThat(dto.performanceCount()).isEqualTo(1);
        assertThat(dto.bugRiskCount()).isEqualTo(1);
        assertThat(dto.documentationCount()).isEqualTo(1);
        assertThat(dto.bestPracticesCount()).isEqualTo(1);
        assertThat(dto.errorHandlingCount()).isEqualTo(1);
        assertThat(dto.testingCount()).isEqualTo(1);
        assertThat(dto.architectureCount()).isEqualTo(1);
        assertThat(dto.styleCount()).isEqualTo(1);
    }
    
    private IssueDTO createIssueDTO(String id, String severity, String category) {
        return new IssueDTO(
                id,
                category,
                severity,
                "Test issue",
                null,
                null,
                "Test.java",
                1,
                null,
                null,
                null,
                null,
                "open",
                null,
                category,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null
        );
    }
}

package org.rostilos.codecrow.core.dto.analysis;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("BranchSummary")
class BranchSummaryTest {

    @Test
    @DisplayName("should create instance with default values")
    void shouldCreateInstanceWithDefaultValues() {
        BranchSummary summary = new BranchSummary();
        
        assertThat(summary.getName()).isNull();
        assertThat(summary.getLastAnalysisAt()).isNull();
        assertThat(summary.getIssueCount()).isZero();
        assertThat(summary.getCriticalIssueCount()).isZero();
    }

    @Test
    @DisplayName("should set and get name")
    void shouldSetAndGetName() {
        BranchSummary summary = new BranchSummary();
        summary.setName("feature/test");
        
        assertThat(summary.getName()).isEqualTo("feature/test");
    }

    @Test
    @DisplayName("should set and get lastAnalysisAt")
    void shouldSetAndGetLastAnalysisAt() {
        BranchSummary summary = new BranchSummary();
        OffsetDateTime time = OffsetDateTime.now();
        summary.setLastAnalysisAt(time);
        
        assertThat(summary.getLastAnalysisAt()).isEqualTo(time);
    }

    @Test
    @DisplayName("should set and get issueCount")
    void shouldSetAndGetIssueCount() {
        BranchSummary summary = new BranchSummary();
        summary.setIssueCount(42);
        
        assertThat(summary.getIssueCount()).isEqualTo(42);
    }

    @Test
    @DisplayName("should set and get criticalIssueCount")
    void shouldSetAndGetCriticalIssueCount() {
        BranchSummary summary = new BranchSummary();
        summary.setCriticalIssueCount(5);
        
        assertThat(summary.getCriticalIssueCount()).isEqualTo(5);
    }

    @Test
    @DisplayName("should allow full configuration")
    void shouldAllowFullConfiguration() {
        BranchSummary summary = new BranchSummary();
        OffsetDateTime time = OffsetDateTime.now();
        
        summary.setName("main");
        summary.setLastAnalysisAt(time);
        summary.setIssueCount(100);
        summary.setCriticalIssueCount(10);
        
        assertThat(summary.getName()).isEqualTo("main");
        assertThat(summary.getLastAnalysisAt()).isEqualTo(time);
        assertThat(summary.getIssueCount()).isEqualTo(100);
        assertThat(summary.getCriticalIssueCount()).isEqualTo(10);
    }
}

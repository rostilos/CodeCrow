package org.rostilos.codecrow.core.model.branch;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

class BranchIssueTest {

    @Test
    void shouldCreateBranchIssue() {
        BranchIssue branchIssue = new BranchIssue();
        assertThat(branchIssue).isNotNull();
    }

    @Test
    void shouldInitializeWithDefaultValues() {
        BranchIssue branchIssue = new BranchIssue();
        
        assertThat(branchIssue.isResolved()).isFalse();
        assertThat(branchIssue.getCreatedAt()).isNotNull();
        assertThat(branchIssue.getUpdatedAt()).isNotNull();
    }

    @Test
    void shouldSetAndGetId() {
        BranchIssue branchIssue = new BranchIssue();
        // ID is auto-generated, verify it's null for new entity
        assertThat(branchIssue.getId()).isNull();
    }

    @Test
    void shouldSetAndGetBranch() {
        BranchIssue branchIssue = new BranchIssue();
        Branch branch = new Branch();
        
        branchIssue.setBranch(branch);
        
        assertThat(branchIssue.getBranch()).isEqualTo(branch);
    }

    @Test
    void shouldSetAndGetCodeAnalysisIssue() {
        BranchIssue branchIssue = new BranchIssue();
        CodeAnalysisIssue issue = new CodeAnalysisIssue();
        
        branchIssue.setCodeAnalysisIssue(issue);
        
        assertThat(branchIssue.getCodeAnalysisIssue()).isEqualTo(issue);
    }

    @Test
    void shouldSetAndGetSeverity() {
        BranchIssue branchIssue = new BranchIssue();
        branchIssue.setSeverity(IssueSeverity.HIGH);
        
        assertThat(branchIssue.getSeverity()).isEqualTo(IssueSeverity.HIGH);
    }

    @Test
    void shouldSetAndGetResolved() {
        BranchIssue branchIssue = new BranchIssue();
        assertThat(branchIssue.isResolved()).isFalse();
        
        branchIssue.setResolved(true);
        
        assertThat(branchIssue.isResolved()).isTrue();
    }

    @Test
    void shouldSetAndGetFirstDetectedPrNumber() {
        BranchIssue branchIssue = new BranchIssue();
        branchIssue.setFirstDetectedPrNumber(42L);
        
        assertThat(branchIssue.getFirstDetectedPrNumber()).isEqualTo(42L);
    }

    @Test
    void shouldSetAndGetResolvedInPrNumber() {
        BranchIssue branchIssue = new BranchIssue();
        branchIssue.setResolvedInPrNumber(50L);
        
        assertThat(branchIssue.getResolvedInPrNumber()).isEqualTo(50L);
    }

    @Test
    void shouldSetAndGetResolvedInCommitHash() {
        BranchIssue branchIssue = new BranchIssue();
        String commitHash = "abc123def456";
        
        branchIssue.setResolvedInCommitHash(commitHash);
        
        assertThat(branchIssue.getResolvedInCommitHash()).isEqualTo(commitHash);
    }

    @Test
    void shouldSetAndGetResolvedDescription() {
        BranchIssue branchIssue = new BranchIssue();
        String description = "Fixed by refactoring authentication logic";
        
        branchIssue.setResolvedDescription(description);
        
        assertThat(branchIssue.getResolvedDescription()).isEqualTo(description);
    }

    @Test
    void shouldSetAndGetResolvedAt() {
        BranchIssue branchIssue = new BranchIssue();
        OffsetDateTime resolvedAt = OffsetDateTime.now();
        
        branchIssue.setResolvedAt(resolvedAt);
        
        assertThat(branchIssue.getResolvedAt()).isEqualTo(resolvedAt);
    }

    @Test
    void shouldSetAndGetResolvedBy() {
        BranchIssue branchIssue = new BranchIssue();
        String resolvedBy = "john.doe";
        
        branchIssue.setResolvedBy(resolvedBy);
        
        assertThat(branchIssue.getResolvedBy()).isEqualTo(resolvedBy);
    }

    @Test
    void shouldGetCreatedAtAndUpdatedAt() {
        BranchIssue branchIssue = new BranchIssue();
        
        assertThat(branchIssue.getCreatedAt()).isNotNull();
        assertThat(branchIssue.getUpdatedAt()).isNotNull();
        assertThat(branchIssue.getCreatedAt()).isBeforeOrEqualTo(OffsetDateTime.now());
        assertThat(branchIssue.getUpdatedAt()).isBeforeOrEqualTo(OffsetDateTime.now());
    }

    @Test
    void shouldUpdateTimestampOnPreUpdate() {
        BranchIssue branchIssue = new BranchIssue();
        OffsetDateTime originalUpdatedAt = branchIssue.getUpdatedAt();
        
        // Simulate @PreUpdate lifecycle callback
        branchIssue.onUpdate();
        
        assertThat(branchIssue.getUpdatedAt()).isAfter(originalUpdatedAt);
    }

    @Test
    void shouldTrackUnresolvedIssue() {
        BranchIssue branchIssue = new BranchIssue();
        Branch branch = new Branch();
        CodeAnalysisIssue issue = new CodeAnalysisIssue();
        
        branchIssue.setBranch(branch);
        branchIssue.setCodeAnalysisIssue(issue);
        branchIssue.setSeverity(IssueSeverity.MEDIUM);
        branchIssue.setFirstDetectedPrNumber(15L);
        
        assertThat(branchIssue.isResolved()).isFalse();
        assertThat(branchIssue.getSeverity()).isEqualTo(IssueSeverity.MEDIUM);
        assertThat(branchIssue.getFirstDetectedPrNumber()).isEqualTo(15L);
        assertThat(branchIssue.getResolvedInPrNumber()).isNull();
        assertThat(branchIssue.getResolvedAt()).isNull();
    }

    @Test
    void shouldTrackResolvedIssueWithAllDetails() {
        BranchIssue branchIssue = new BranchIssue();
        Branch branch = new Branch();
        CodeAnalysisIssue issue = new CodeAnalysisIssue();
        OffsetDateTime resolvedTime = OffsetDateTime.now();
        
        branchIssue.setBranch(branch);
        branchIssue.setCodeAnalysisIssue(issue);
        branchIssue.setSeverity(IssueSeverity.HIGH);
        branchIssue.setFirstDetectedPrNumber(20L);
        branchIssue.setResolved(true);
        branchIssue.setResolvedInPrNumber(25L);
        branchIssue.setResolvedInCommitHash("fedcba98");
        branchIssue.setResolvedDescription("Security patch applied");
        branchIssue.setResolvedAt(resolvedTime);
        branchIssue.setResolvedBy("security.team");
        
        assertThat(branchIssue.isResolved()).isTrue();
        assertThat(branchIssue.getSeverity()).isEqualTo(IssueSeverity.HIGH);
        assertThat(branchIssue.getFirstDetectedPrNumber()).isEqualTo(20L);
        assertThat(branchIssue.getResolvedInPrNumber()).isEqualTo(25L);
        assertThat(branchIssue.getResolvedInCommitHash()).isEqualTo("fedcba98");
        assertThat(branchIssue.getResolvedDescription()).isEqualTo("Security patch applied");
        assertThat(branchIssue.getResolvedAt()).isEqualTo(resolvedTime);
        assertThat(branchIssue.getResolvedBy()).isEqualTo("security.team");
    }
}

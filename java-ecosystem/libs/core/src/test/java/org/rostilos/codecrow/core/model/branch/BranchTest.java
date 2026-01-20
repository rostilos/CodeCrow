package org.rostilos.codecrow.core.model.branch;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class BranchTest {

    @Test
    void shouldCreateBranch() {
        Branch branch = new Branch();
        assertThat(branch).isNotNull();
    }

    @Test
    void shouldInitializeWithDefaultValues() {
        Branch branch = new Branch();
        
        assertThat(branch.getTotalIssues()).isEqualTo(0);
        assertThat(branch.getHighSeverityCount()).isEqualTo(0);
        assertThat(branch.getMediumSeverityCount()).isEqualTo(0);
        assertThat(branch.getLowSeverityCount()).isEqualTo(0);
        assertThat(branch.getInfoSeverityCount()).isEqualTo(0);
        assertThat(branch.getResolvedCount()).isEqualTo(0);
        assertThat(branch.getCreatedAt()).isNotNull();
        assertThat(branch.getUpdatedAt()).isNotNull();
        assertThat(branch.getIssues()).isEmpty();
    }

    @Test
    void shouldSetAndGetProject() {
        Branch branch = new Branch();
        Project project = new Project();
        
        branch.setProject(project);
        
        assertThat(branch.getProject()).isEqualTo(project);
    }

    @Test
    void shouldSetAndGetBranchName() {
        Branch branch = new Branch();
        branch.setBranchName("main");
        
        assertThat(branch.getBranchName()).isEqualTo("main");
    }

    @Test
    void shouldSetAndGetCommitHash() {
        Branch branch = new Branch();
        String commitHash = "abc123def456";
        
        branch.setCommitHash(commitHash);
        
        assertThat(branch.getCommitHash()).isEqualTo(commitHash);
    }

    @Test
    void shouldUpdateTimestampOnPreUpdate() {
        Branch branch = new Branch();
        OffsetDateTime originalUpdatedAt = branch.getUpdatedAt();
        
        branch.onUpdate();
        
        assertThat(branch.getUpdatedAt()).isAfter(originalUpdatedAt);
    }

    @Test
    void shouldUpdateIssueCountsWhenSettingIssues() {
        Branch branch = new Branch();
        List<BranchIssue> issues = new ArrayList<>();
        
        BranchIssue highIssue = createBranchIssue(branch, IssueSeverity.HIGH, false);
        BranchIssue mediumIssue = createBranchIssue(branch, IssueSeverity.MEDIUM, false);
        BranchIssue lowIssue = createBranchIssue(branch, IssueSeverity.LOW, false);
        BranchIssue resolvedIssue = createBranchIssue(branch, IssueSeverity.HIGH, true);
        
        issues.add(highIssue);
        issues.add(mediumIssue);
        issues.add(lowIssue);
        issues.add(resolvedIssue);
        
        branch.setIssues(issues);
        
        assertThat(branch.getTotalIssues()).isEqualTo(3);
        assertThat(branch.getHighSeverityCount()).isEqualTo(1);
        assertThat(branch.getMediumSeverityCount()).isEqualTo(1);
        assertThat(branch.getLowSeverityCount()).isEqualTo(1);
        assertThat(branch.getResolvedCount()).isEqualTo(1);
    }

    @Test
    void shouldUpdateIssueCountsWithMultipleHighSeverity() {
        Branch branch = new Branch();
        List<BranchIssue> issues = new ArrayList<>();
        
        issues.add(createBranchIssue(branch, IssueSeverity.HIGH, false));
        issues.add(createBranchIssue(branch, IssueSeverity.HIGH, false));
        issues.add(createBranchIssue(branch, IssueSeverity.MEDIUM, false));
        
        branch.setIssues(issues);
        
        assertThat(branch.getTotalIssues()).isEqualTo(3);
        assertThat(branch.getHighSeverityCount()).isEqualTo(2);
        assertThat(branch.getMediumSeverityCount()).isEqualTo(1);
    }

    @Test
    void shouldUpdateIssueCountsWithInfoSeverity() {
        Branch branch = new Branch();
        List<BranchIssue> issues = new ArrayList<>();
        
        issues.add(createBranchIssue(branch, IssueSeverity.INFO, false));
        issues.add(createBranchIssue(branch, IssueSeverity.INFO, false));
        
        branch.setIssues(issues);
        
        assertThat(branch.getTotalIssues()).isEqualTo(2);
        assertThat(branch.getInfoSeverityCount()).isEqualTo(2);
    }

    @Test
    void shouldExcludeResolvedIssuesFromSeverityCounts() {
        Branch branch = new Branch();
        List<BranchIssue> issues = new ArrayList<>();
        
        issues.add(createBranchIssue(branch, IssueSeverity.HIGH, false));
        issues.add(createBranchIssue(branch, IssueSeverity.HIGH, true));
        issues.add(createBranchIssue(branch, IssueSeverity.MEDIUM, true));
        
        branch.setIssues(issues);
        
        assertThat(branch.getTotalIssues()).isEqualTo(1);
        assertThat(branch.getHighSeverityCount()).isEqualTo(1);
        assertThat(branch.getMediumSeverityCount()).isEqualTo(0);
        assertThat(branch.getResolvedCount()).isEqualTo(2);
    }

    @Test
    void shouldCallUpdateIssueCountsDirectly() {
        Branch branch = new Branch();
        List<BranchIssue> issues = branch.getIssues();
        
        issues.add(createBranchIssue(branch, IssueSeverity.HIGH, false));
        issues.add(createBranchIssue(branch, IssueSeverity.LOW, false));
        
        branch.updateIssueCounts();
        
        assertThat(branch.getTotalIssues()).isEqualTo(2);
        assertThat(branch.getHighSeverityCount()).isEqualTo(1);
        assertThat(branch.getLowSeverityCount()).isEqualTo(1);
    }

    private BranchIssue createBranchIssue(Branch branch, IssueSeverity severity, boolean resolved) {
        BranchIssue issue = new BranchIssue();
        issue.setBranch(branch);
        issue.setSeverity(severity);
        issue.setResolved(resolved);
        return issue;
    }
}

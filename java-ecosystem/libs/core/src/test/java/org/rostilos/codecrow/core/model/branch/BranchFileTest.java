package org.rostilos.codecrow.core.model.branch;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

class BranchFileTest {

    @Test
    void shouldCreateBranchFile() {
        BranchFile branchFile = new BranchFile();
        assertThat(branchFile).isNotNull();
    }

    @Test
    void shouldInitializeWithDefaultValues() {
        BranchFile branchFile = new BranchFile();
        
        assertThat(branchFile.getIssueCount()).isEqualTo(0);
        assertThat(branchFile.getCreatedAt()).isNotNull();
        assertThat(branchFile.getUpdatedAt()).isNotNull();
    }

    @Test
    void shouldSetAndGetId() {
        BranchFile branchFile = new BranchFile();
        // ID is auto-generated, so we can't set it directly through setter
        // Just verify getId returns null for new entity
        assertThat(branchFile.getId()).isNull();
    }

    @Test
    void shouldSetAndGetProject() {
        BranchFile branchFile = new BranchFile();
        Project project = new Project();
        
        branchFile.setProject(project);
        
        assertThat(branchFile.getProject()).isEqualTo(project);
    }

    @Test
    void shouldSetAndGetBranchName() {
        BranchFile branchFile = new BranchFile();
        branchFile.setBranchName("feature/new-feature");
        
        assertThat(branchFile.getBranchName()).isEqualTo("feature/new-feature");
    }

    @Test
    void shouldSetAndGetFilePath() {
        BranchFile branchFile = new BranchFile();
        branchFile.setFilePath("src/main/java/com/example/App.java");
        
        assertThat(branchFile.getFilePath()).isEqualTo("src/main/java/com/example/App.java");
    }

    @Test
    void shouldSetAndGetIssueCount() {
        BranchFile branchFile = new BranchFile();
        branchFile.setIssueCount(5);
        
        assertThat(branchFile.getIssueCount()).isEqualTo(5);
    }

    @Test
    void shouldIncrementIssueCount() {
        BranchFile branchFile = new BranchFile();
        assertThat(branchFile.getIssueCount()).isEqualTo(0);
        
        branchFile.setIssueCount(branchFile.getIssueCount() + 1);
        assertThat(branchFile.getIssueCount()).isEqualTo(1);
        
        branchFile.setIssueCount(branchFile.getIssueCount() + 3);
        assertThat(branchFile.getIssueCount()).isEqualTo(4);
    }

    @Test
    void shouldUpdateTimestampOnPreUpdate() {
        BranchFile branchFile = new BranchFile();
        OffsetDateTime originalUpdatedAt = branchFile.getUpdatedAt();
        
        // Simulate @PreUpdate lifecycle callback
        branchFile.onUpdate();
        
        assertThat(branchFile.getUpdatedAt()).isAfter(originalUpdatedAt);
    }

    @Test
    void shouldMaintainCreatedAtAfterUpdate() {
        BranchFile branchFile = new BranchFile();
        OffsetDateTime createdAt = branchFile.getCreatedAt();
        
        branchFile.onUpdate();
        branchFile.setIssueCount(10);
        
        assertThat(branchFile.getCreatedAt()).isEqualTo(createdAt);
    }

    @Test
    void shouldSetAllFields() {
        BranchFile branchFile = new BranchFile();
        Project project = new Project();
        
        branchFile.setProject(project);
        branchFile.setBranchName("develop");
        branchFile.setFilePath("src/test/UserTest.java");
        branchFile.setIssueCount(12);
        
        assertThat(branchFile.getProject()).isEqualTo(project);
        assertThat(branchFile.getBranchName()).isEqualTo("develop");
        assertThat(branchFile.getFilePath()).isEqualTo("src/test/UserTest.java");
        assertThat(branchFile.getIssueCount()).isEqualTo(12);
        assertThat(branchFile.getCreatedAt()).isNotNull();
        assertThat(branchFile.getUpdatedAt()).isNotNull();
    }
}

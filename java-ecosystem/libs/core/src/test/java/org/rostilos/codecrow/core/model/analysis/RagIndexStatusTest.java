package org.rostilos.codecrow.core.model.analysis;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

class RagIndexStatusTest {

    @Test
    void shouldCreateRagIndexStatus() {
        RagIndexStatus status = new RagIndexStatus();
        assertThat(status).isNotNull();
    }

    @Test
    void shouldInitializeWithDefaultValues() {
        RagIndexStatus status = new RagIndexStatus();
        
        assertThat(status.getCreatedAt()).isNotNull();
        assertThat(status.getUpdatedAt()).isNotNull();
        assertThat(status.getFailedIncrementalCount()).isEqualTo(0);
    }

    @Test
    void shouldSetAndGetId() {
        RagIndexStatus status = new RagIndexStatus();
        status.setId(100L);
        
        assertThat(status.getId()).isEqualTo(100L);
    }

    @Test
    void shouldSetAndGetProject() {
        RagIndexStatus status = new RagIndexStatus();
        Project project = new Project();
        
        status.setProject(project);
        
        assertThat(status.getProject()).isEqualTo(project);
    }

    @Test
    void shouldSetAndGetWorkspaceName() {
        RagIndexStatus status = new RagIndexStatus();
        status.setWorkspaceName("my-workspace");
        
        assertThat(status.getWorkspaceName()).isEqualTo("my-workspace");
    }

    @Test
    void shouldSetAndGetProjectName() {
        RagIndexStatus status = new RagIndexStatus();
        status.setProjectName("my-project");
        
        assertThat(status.getProjectName()).isEqualTo("my-project");
    }

    @Test
    void shouldSetAndGetStatus() {
        RagIndexStatus status = new RagIndexStatus();
        status.setStatus(RagIndexingStatus.INDEXED);
        
        assertThat(status.getStatus()).isEqualTo(RagIndexingStatus.INDEXED);
    }

    @Test
    void shouldSetAndGetIndexedBranch() {
        RagIndexStatus status = new RagIndexStatus();
        status.setIndexedBranch("main");
        
        assertThat(status.getIndexedBranch()).isEqualTo("main");
    }

    @Test
    void shouldSetAndGetIndexedCommitHash() {
        RagIndexStatus status = new RagIndexStatus();
        String commitHash = "abc123def456";
        
        status.setIndexedCommitHash(commitHash);
        
        assertThat(status.getIndexedCommitHash()).isEqualTo(commitHash);
    }

    @Test
    void shouldSetAndGetTotalFilesIndexed() {
        RagIndexStatus status = new RagIndexStatus();
        status.setTotalFilesIndexed(250);
        
        assertThat(status.getTotalFilesIndexed()).isEqualTo(250);
    }

    @Test
    void shouldSetAndGetLastIndexedAt() {
        RagIndexStatus status = new RagIndexStatus();
        OffsetDateTime lastIndexed = OffsetDateTime.now();
        
        status.setLastIndexedAt(lastIndexed);
        
        assertThat(status.getLastIndexedAt()).isEqualTo(lastIndexed);
    }

    @Test
    void shouldSetAndGetCreatedAt() {
        RagIndexStatus status = new RagIndexStatus();
        OffsetDateTime created = OffsetDateTime.now().minusDays(1);
        
        status.setCreatedAt(created);
        
        assertThat(status.getCreatedAt()).isEqualTo(created);
    }

    @Test
    void shouldSetAndGetUpdatedAt() {
        RagIndexStatus status = new RagIndexStatus();
        OffsetDateTime updated = OffsetDateTime.now();
        
        status.setUpdatedAt(updated);
        
        assertThat(status.getUpdatedAt()).isEqualTo(updated);
    }

    @Test
    void shouldSetAndGetErrorMessage() {
        RagIndexStatus status = new RagIndexStatus();
        String errorMessage = "Failed to index large file";
        
        status.setErrorMessage(errorMessage);
        
        assertThat(status.getErrorMessage()).isEqualTo(errorMessage);
    }

    @Test
    void shouldSetAndGetCollectionName() {
        RagIndexStatus status = new RagIndexStatus();
        String collectionName = "workspace_project_main";
        
        status.setCollectionName(collectionName);
        
        assertThat(status.getCollectionName()).isEqualTo(collectionName);
    }

    @Test
    void shouldSetAndGetFailedIncrementalCount() {
        RagIndexStatus status = new RagIndexStatus();
        status.setFailedIncrementalCount(3);
        
        assertThat(status.getFailedIncrementalCount()).isEqualTo(3);
    }

    @Test
    void shouldIncrementFailedIncrementalCount() {
        RagIndexStatus status = new RagIndexStatus();
        assertThat(status.getFailedIncrementalCount()).isEqualTo(0);
        
        status.incrementFailedIncrementalCount();
        
        assertThat(status.getFailedIncrementalCount()).isEqualTo(1);
    }

    @Test
    void shouldIncrementFailedIncrementalCountMultipleTimes() {
        RagIndexStatus status = new RagIndexStatus();
        
        status.incrementFailedIncrementalCount();
        status.incrementFailedIncrementalCount();
        status.incrementFailedIncrementalCount();
        
        assertThat(status.getFailedIncrementalCount()).isEqualTo(3);
    }

    @Test
    void shouldResetFailedIncrementalCount() {
        RagIndexStatus status = new RagIndexStatus();
        status.setFailedIncrementalCount(5);
        
        status.resetFailedIncrementalCount();
        
        assertThat(status.getFailedIncrementalCount()).isEqualTo(0);
    }

    @Test
    void shouldUpdateTimestampOnPreUpdate() {
        RagIndexStatus status = new RagIndexStatus();
        OffsetDateTime originalUpdatedAt = status.getUpdatedAt();
        
        // Simulate @PreUpdate lifecycle callback
        status.onUpdate();
        
        assertThat(status.getUpdatedAt()).isAfter(originalUpdatedAt);
    }

    @Test
    void shouldHandleNullFailedIncrementalCount() {
        RagIndexStatus status = new RagIndexStatus();
        status.setFailedIncrementalCount(null);
        
        assertThat(status.getFailedIncrementalCount()).isEqualTo(0);
    }

    @Test
    void shouldIncrementWhenFailedIncrementalCountIsNull() {
        RagIndexStatus status = new RagIndexStatus();
        status.setFailedIncrementalCount(null);
        
        status.incrementFailedIncrementalCount();
        
        assertThat(status.getFailedIncrementalCount()).isEqualTo(1);
    }

    @Test
    void shouldTrackCompleteIndexingStatus() {
        RagIndexStatus status = new RagIndexStatus();
        Project project = new Project();
        OffsetDateTime lastIndexed = OffsetDateTime.now();
        
        status.setProject(project);
        status.setWorkspaceName("acme-workspace");
        status.setProjectName("backend-api");
        status.setStatus(RagIndexingStatus.INDEXED);
        status.setIndexedBranch("develop");
        status.setIndexedCommitHash("fedcba987654");
        status.setTotalFilesIndexed(450);
        status.setLastIndexedAt(lastIndexed);
        status.setCollectionName("acme_backend_develop");
        status.setFailedIncrementalCount(0);
        
        assertThat(status.getProject()).isEqualTo(project);
        assertThat(status.getWorkspaceName()).isEqualTo("acme-workspace");
        assertThat(status.getProjectName()).isEqualTo("backend-api");
        assertThat(status.getStatus()).isEqualTo(RagIndexingStatus.INDEXED);
        assertThat(status.getIndexedBranch()).isEqualTo("develop");
        assertThat(status.getIndexedCommitHash()).isEqualTo("fedcba987654");
        assertThat(status.getTotalFilesIndexed()).isEqualTo(450);
        assertThat(status.getLastIndexedAt()).isEqualTo(lastIndexed);
        assertThat(status.getCollectionName()).isEqualTo("acme_backend_develop");
        assertThat(status.getFailedIncrementalCount()).isEqualTo(0);
        assertThat(status.getErrorMessage()).isNull();
    }

    @Test
    void shouldTrackFailedIndexingStatus() {
        RagIndexStatus status = new RagIndexStatus();
        
        status.setStatus(RagIndexingStatus.FAILED);
        status.setErrorMessage("Timeout while connecting to RAG service");
        status.setFailedIncrementalCount(2);
        
        assertThat(status.getStatus()).isEqualTo(RagIndexingStatus.FAILED);
        assertThat(status.getErrorMessage()).isEqualTo("Timeout while connecting to RAG service");
        assertThat(status.getFailedIncrementalCount()).isEqualTo(2);
    }
}

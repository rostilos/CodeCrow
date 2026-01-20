package org.rostilos.codecrow.core.model.rag;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

@DisplayName("RagDeltaIndex Entity Tests")
class RagDeltaIndexTest {

    private RagDeltaIndex ragDeltaIndex;

    @BeforeEach
    void setUp() {
        ragDeltaIndex = new RagDeltaIndex();
    }

    @Nested
    @DisplayName("Constructor tests")
    class ConstructorTests {

        @Test
        @DisplayName("Default constructor should create object with defaults")
        void defaultConstructorShouldCreateObjectWithDefaults() {
            RagDeltaIndex index = new RagDeltaIndex();
            
            assertThat(index.getId()).isNull();
            assertThat(index.getProject()).isNull();
            assertThat(index.getBranchName()).isNull();
        }

        @Test
        @DisplayName("Parameterized constructor should set all fields")
        void parameterizedConstructorShouldSetAllFields() {
            Project project = mock(Project.class);
            
            RagDeltaIndex index = new RagDeltaIndex(project, "feature/branch", "main", "collection-123");
            
            assertThat(index.getProject()).isSameAs(project);
            assertThat(index.getBranchName()).isEqualTo("feature/branch");
            assertThat(index.getBaseBranch()).isEqualTo("main");
            assertThat(index.getCollectionName()).isEqualTo("collection-123");
            assertThat(index.getStatus()).isEqualTo(DeltaIndexStatus.CREATING);
        }
    }

    @Nested
    @DisplayName("Getter and Setter tests")
    class GetterSetterTests {

        @Test
        @DisplayName("Should set and get id")
        void shouldSetAndGetId() {
            ragDeltaIndex.setId(100L);
            assertThat(ragDeltaIndex.getId()).isEqualTo(100L);
        }

        @Test
        @DisplayName("Should set and get project")
        void shouldSetAndGetProject() {
            Project project = mock(Project.class);
            ragDeltaIndex.setProject(project);
            assertThat(ragDeltaIndex.getProject()).isSameAs(project);
        }

        @Test
        @DisplayName("Should set and get branchName")
        void shouldSetAndGetBranchName() {
            ragDeltaIndex.setBranchName("release/1.0");
            assertThat(ragDeltaIndex.getBranchName()).isEqualTo("release/1.0");
        }

        @Test
        @DisplayName("Should set and get baseBranch")
        void shouldSetAndGetBaseBranch() {
            ragDeltaIndex.setBaseBranch("master");
            assertThat(ragDeltaIndex.getBaseBranch()).isEqualTo("master");
        }

        @Test
        @DisplayName("Should set and get baseCommitHash")
        void shouldSetAndGetBaseCommitHash() {
            ragDeltaIndex.setBaseCommitHash("abc123def456");
            assertThat(ragDeltaIndex.getBaseCommitHash()).isEqualTo("abc123def456");
        }

        @Test
        @DisplayName("Should set and get deltaCommitHash")
        void shouldSetAndGetDeltaCommitHash() {
            ragDeltaIndex.setDeltaCommitHash("789xyz000");
            assertThat(ragDeltaIndex.getDeltaCommitHash()).isEqualTo("789xyz000");
        }

        @Test
        @DisplayName("Should set and get collectionName")
        void shouldSetAndGetCollectionName() {
            ragDeltaIndex.setCollectionName("my-collection");
            assertThat(ragDeltaIndex.getCollectionName()).isEqualTo("my-collection");
        }

        @Test
        @DisplayName("Should set and get chunkCount")
        void shouldSetAndGetChunkCount() {
            ragDeltaIndex.setChunkCount(150);
            assertThat(ragDeltaIndex.getChunkCount()).isEqualTo(150);
        }

        @Test
        @DisplayName("Should set and get fileCount")
        void shouldSetAndGetFileCount() {
            ragDeltaIndex.setFileCount(25);
            assertThat(ragDeltaIndex.getFileCount()).isEqualTo(25);
        }

        @Test
        @DisplayName("Should set and get status")
        void shouldSetAndGetStatus() {
            ragDeltaIndex.setStatus(DeltaIndexStatus.READY);
            assertThat(ragDeltaIndex.getStatus()).isEqualTo(DeltaIndexStatus.READY);
        }

        @Test
        @DisplayName("Should set and get errorMessage")
        void shouldSetAndGetErrorMessage() {
            ragDeltaIndex.setErrorMessage("Index creation failed");
            assertThat(ragDeltaIndex.getErrorMessage()).isEqualTo("Index creation failed");
        }

        @Test
        @DisplayName("Should set and get createdAt")
        void shouldSetAndGetCreatedAt() {
            OffsetDateTime time = OffsetDateTime.now();
            ragDeltaIndex.setCreatedAt(time);
            assertThat(ragDeltaIndex.getCreatedAt()).isEqualTo(time);
        }

        @Test
        @DisplayName("Should set and get updatedAt")
        void shouldSetAndGetUpdatedAt() {
            OffsetDateTime time = OffsetDateTime.now();
            ragDeltaIndex.setUpdatedAt(time);
            assertThat(ragDeltaIndex.getUpdatedAt()).isEqualTo(time);
        }

        @Test
        @DisplayName("Should set and get lastAccessedAt")
        void shouldSetAndGetLastAccessedAt() {
            OffsetDateTime time = OffsetDateTime.now();
            ragDeltaIndex.setLastAccessedAt(time);
            assertThat(ragDeltaIndex.getLastAccessedAt()).isEqualTo(time);
        }
    }

    @Nested
    @DisplayName("Business logic tests")
    class BusinessLogicTests {

        @Test
        @DisplayName("markAccessed should update lastAccessedAt")
        void markAccessedShouldUpdateLastAccessedAt() {
            assertThat(ragDeltaIndex.getLastAccessedAt()).isNull();
            
            ragDeltaIndex.markAccessed();
            
            assertThat(ragDeltaIndex.getLastAccessedAt()).isNotNull();
            assertThat(ragDeltaIndex.getLastAccessedAt()).isBeforeOrEqualTo(OffsetDateTime.now());
        }

        @Test
        @DisplayName("isReady should return true when status is READY")
        void isReadyShouldReturnTrueWhenStatusIsReady() {
            ragDeltaIndex.setStatus(DeltaIndexStatus.READY);
            assertThat(ragDeltaIndex.isReady()).isTrue();
        }

        @Test
        @DisplayName("isReady should return false when status is not READY")
        void isReadyShouldReturnFalseWhenStatusIsNotReady() {
            ragDeltaIndex.setStatus(DeltaIndexStatus.CREATING);
            assertThat(ragDeltaIndex.isReady()).isFalse();
            
            ragDeltaIndex.setStatus(DeltaIndexStatus.STALE);
            assertThat(ragDeltaIndex.isReady()).isFalse();
            
            ragDeltaIndex.setStatus(DeltaIndexStatus.FAILED);
            assertThat(ragDeltaIndex.isReady()).isFalse();
            
            ragDeltaIndex.setStatus(DeltaIndexStatus.ARCHIVED);
            assertThat(ragDeltaIndex.isReady()).isFalse();
        }

        @Test
        @DisplayName("needsRebuild should return true when status is STALE")
        void needsRebuildShouldReturnTrueWhenStatusIsStale() {
            ragDeltaIndex.setStatus(DeltaIndexStatus.STALE);
            assertThat(ragDeltaIndex.needsRebuild()).isTrue();
        }

        @Test
        @DisplayName("needsRebuild should return true when status is FAILED")
        void needsRebuildShouldReturnTrueWhenStatusIsFailed() {
            ragDeltaIndex.setStatus(DeltaIndexStatus.FAILED);
            assertThat(ragDeltaIndex.needsRebuild()).isTrue();
        }

        @Test
        @DisplayName("needsRebuild should return false for other statuses")
        void needsRebuildShouldReturnFalseForOtherStatuses() {
            ragDeltaIndex.setStatus(DeltaIndexStatus.READY);
            assertThat(ragDeltaIndex.needsRebuild()).isFalse();
            
            ragDeltaIndex.setStatus(DeltaIndexStatus.CREATING);
            assertThat(ragDeltaIndex.needsRebuild()).isFalse();
            
            ragDeltaIndex.setStatus(DeltaIndexStatus.ARCHIVED);
            assertThat(ragDeltaIndex.needsRebuild()).isFalse();
        }
    }

    @Nested
    @DisplayName("toString tests")
    class ToStringTests {

        @Test
        @DisplayName("toString should contain all key fields")
        void toStringShouldContainAllKeyFields() {
            ragDeltaIndex.setId(1L);
            ragDeltaIndex.setBranchName("feature/test");
            ragDeltaIndex.setBaseBranch("main");
            ragDeltaIndex.setStatus(DeltaIndexStatus.READY);
            ragDeltaIndex.setChunkCount(100);
            
            String result = ragDeltaIndex.toString();
            
            assertThat(result).contains("id=1");
            assertThat(result).contains("branchName='feature/test'");
            assertThat(result).contains("baseBranch='main'");
            assertThat(result).contains("status=READY");
            assertThat(result).contains("chunkCount=100");
        }
    }
}

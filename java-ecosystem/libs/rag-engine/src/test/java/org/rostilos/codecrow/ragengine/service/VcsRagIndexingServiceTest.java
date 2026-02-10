package org.rostilos.codecrow.ragengine.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.core.dto.project.ProjectDTO;
import org.rostilos.codecrow.core.model.analysis.RagIndexStatus;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.RagConfig;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.service.AnalysisJobService;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.Map;
import java.util.Optional;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("VcsRagIndexingService")
class VcsRagIndexingServiceTest {

    @Mock
    private ProjectRepository projectRepository;
    @Mock
    private VcsClientProvider vcsClientProvider;
    @Mock
    private RagIndexingService ragIndexingService;
    @Mock
    private RagIndexTrackingService ragIndexTrackingService;
    @Mock
    private AnalysisLockService analysisLockService;
    @Mock
    private AnalysisJobService jobService;

    private VcsRagIndexingService service;
    private Project testProject;
    @Mock
    private Consumer<Map<String, Object>> messageConsumer;

    @BeforeEach
    void setUp() {
        service = new VcsRagIndexingService(
                projectRepository, vcsClientProvider, ragIndexingService,
                ragIndexTrackingService, analysisLockService, jobService
        );
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);

        testProject = new Project();
        ReflectionTestUtils.setField(testProject, "id", 100L);
        testProject.setName("test-project");
    }

    private ProjectDTO createProjectDTO(Long id) {
        return new ProjectDTO(id, null, null, false, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null);
    }

    @Nested
    @DisplayName("indexProjectFromVcs()")
    class IndexProjectFromVcsTests {

        @Test
        @DisplayName("should skip when RAG API is disabled")
        void shouldSkipWhenApiDisabled() {
            ReflectionTestUtils.setField(service, "ragApiEnabled", false);

            Map<String, Object> result = service.indexProjectFromVcs(createProjectDTO(100L), "main", messageConsumer);

            assertThat(result).containsEntry("status", "skipped");
            verifyNoInteractions(projectRepository);
        }

        @Test
        @DisplayName("should return error when RAG pipeline not available")
        void shouldReturnErrorWhenPipelineUnavailable() {
            when(ragIndexingService.isAvailable()).thenReturn(false);

            Map<String, Object> result = service.indexProjectFromVcs(createProjectDTO(100L), "main", messageConsumer);

            assertThat(result).containsEntry("status", "error");
        }

        @Test
        @DisplayName("should skip when RAG not enabled in config")
        void shouldSkipWhenRagNotEnabled() {
            when(ragIndexingService.isAvailable()).thenReturn(true);
            testProject.setConfiguration(new ProjectConfig(false, "main", null, new RagConfig(false)));
            when(projectRepository.findByIdWithFullDetails(100L)).thenReturn(Optional.of(testProject));

            Map<String, Object> result = service.indexProjectFromVcs(createProjectDTO(100L), "main", messageConsumer);

            assertThat(result).containsEntry("status", "skipped");
        }

        @Test
        @DisplayName("should skip when config is null")
        void shouldSkipWhenConfigIsNull() {
            when(ragIndexingService.isAvailable()).thenReturn(true);
            when(projectRepository.findByIdWithFullDetails(100L)).thenReturn(Optional.of(testProject));

            Map<String, Object> result = service.indexProjectFromVcs(createProjectDTO(100L), "main", messageConsumer);

            assertThat(result).containsEntry("status", "skipped");
        }

        @Test
        @DisplayName("should return locked when indexing already in progress")
        void shouldReturnLockedWhenAlreadyIndexing() {
            setupProjectWithRagEnabled();
            setupProjectWithVcsBinding();
            when(ragIndexingService.isAvailable()).thenReturn(true);
            when(projectRepository.findByIdWithFullDetails(100L)).thenReturn(Optional.of(testProject));
            when(ragIndexTrackingService.canStartIndexing(testProject)).thenReturn(false);

            Map<String, Object> result = service.indexProjectFromVcs(createProjectDTO(100L), "main", messageConsumer);

            assertThat(result).containsEntry("status", "locked");
        }

        @Test
        @DisplayName("should return locked when lock acquisition fails")
        void shouldReturnLockedWhenLockFails() {
            setupProjectWithRagEnabled();
            setupProjectWithVcsBinding();
            when(ragIndexingService.isAvailable()).thenReturn(true);
            when(projectRepository.findByIdWithFullDetails(100L)).thenReturn(Optional.of(testProject));
            when(ragIndexTrackingService.canStartIndexing(testProject)).thenReturn(true);
            when(analysisLockService.acquireLock(any(), anyString(), any())).thenReturn(Optional.empty());

            Map<String, Object> result = service.indexProjectFromVcs(createProjectDTO(100L), "main", messageConsumer);

            assertThat(result).containsEntry("status", "locked");
        }
    }

    @Nested
    @DisplayName("shouldAutoIndex()")
    class ShouldAutoIndexTests {

        @Test
        @DisplayName("should return false when API is disabled")
        void shouldReturnFalseWhenDisabled() {
            ReflectionTestUtils.setField(service, "ragApiEnabled", false);
            assertThat(service.shouldAutoIndex(testProject)).isFalse();
        }

        @Test
        @DisplayName("should return false when config is null")
        void shouldReturnFalseWhenConfigNull() {
            assertThat(service.shouldAutoIndex(testProject)).isFalse();
        }

        @Test
        @DisplayName("should return false when RAG not enabled in config")
        void shouldReturnFalseWhenRagDisabledInConfig() {
            testProject.setConfiguration(new ProjectConfig(false, "main", null, new RagConfig(false)));
            assertThat(service.shouldAutoIndex(testProject)).isFalse();
        }

        @Test
        @DisplayName("should return false when already indexed")
        void shouldReturnFalseWhenAlreadyIndexed() {
            setupProjectWithRagEnabled();
            RagIndexStatus status = mock(RagIndexStatus.class);
            when(ragIndexTrackingService.getIndexStatus(testProject)).thenReturn(Optional.of(status));

            assertThat(service.shouldAutoIndex(testProject)).isFalse();
        }

        @Test
        @DisplayName("should return true when all conditions met")
        void shouldReturnTrueWhenAllConditionsMet() {
            setupProjectWithRagEnabled();
            setupProjectWithVcsBinding();
            when(ragIndexTrackingService.getIndexStatus(testProject)).thenReturn(Optional.empty());

            assertThat(service.shouldAutoIndex(testProject)).isTrue();
        }
    }

    private void setupProjectWithRagEnabled() {
        RagConfig ragConfig = new RagConfig(true, "main");
        ProjectConfig config = new ProjectConfig(false, "main", null, ragConfig);
        testProject.setConfiguration(config);
    }

    private void setupProjectWithVcsBinding() {
        VcsRepoBinding binding = new VcsRepoBinding();
        VcsConnection connection = new VcsConnection();
        binding.setVcsConnection(connection);
        binding.setExternalNamespace("my-workspace");
        binding.setExternalRepoSlug("my-repo");
        testProject.setVcsRepoBinding(binding);
    }

    private void setupProjectWorkspace() {
        Workspace workspace = new Workspace();
        workspace.setName("test-ws");
        testProject.setWorkspace(workspace);
        testProject.setNamespace("test-ns");
    }

    // ── performIndexing full-flow tests ─────────────────────────────────

    @Nested
    @DisplayName("indexProjectFromVcs() - full indexing flow")
    class FullIndexingFlowTests {

        @Test
        @DisplayName("should complete full indexing successfully")
        void shouldCompleteFullIndexing() throws Exception {
            setupProjectWithRagEnabled();
            setupProjectWithVcsBinding();
            setupProjectWorkspace();

            when(ragIndexingService.isAvailable()).thenReturn(true);
            when(projectRepository.findByIdWithFullDetails(100L)).thenReturn(Optional.of(testProject));
            when(ragIndexTrackingService.canStartIndexing(testProject)).thenReturn(true);
            when(analysisLockService.acquireLock(any(), anyString(), any())).thenReturn(Optional.of("lock-key"));

            Job mockJob = mock(Job.class);
            when(jobService.createRagIndexJob(any(), isNull())).thenReturn(mockJob);

            VcsClient mockVcs = mock(VcsClient.class);
            when(vcsClientProvider.getClient(any())).thenReturn(mockVcs);
            when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "main")).thenReturn("abc123");
            when(mockVcs.downloadRepositoryArchiveToFile(eq("my-workspace"), eq("my-repo"), eq("main"), any()))
                    .thenReturn(2048L);

            when(ragIndexingService.indexFromArchiveFile(any(), eq("test-ws"), eq("test-ns"), eq("main"), eq("abc123"), isNull()))
                    .thenReturn(Map.of("document_count", 42));

            Map<String, Object> result = service.indexProjectFromVcs(createProjectDTO(100L), "main", messageConsumer);

            assertThat(result).containsEntry("status", "completed");
            assertThat(result).containsEntry("filesIndexed", 42);
            assertThat(result).containsEntry("branch", "main");
            verify(ragIndexTrackingService).markIndexingStarted(testProject, "main", "abc123");
            verify(ragIndexTrackingService).markIndexingCompleted(testProject, "main", "abc123", 42);
            verify(jobService).completeJob(eq(mockJob), isNull());
            verify(analysisLockService).releaseLock("lock-key");
        }

        @Test
        @DisplayName("should handle IOException during indexing")
        void shouldHandleIndexingIOException() throws Exception {
            setupProjectWithRagEnabled();
            setupProjectWithVcsBinding();
            setupProjectWorkspace();

            when(ragIndexingService.isAvailable()).thenReturn(true);
            when(projectRepository.findByIdWithFullDetails(100L)).thenReturn(Optional.of(testProject));
            when(ragIndexTrackingService.canStartIndexing(testProject)).thenReturn(true);
            when(analysisLockService.acquireLock(any(), anyString(), any())).thenReturn(Optional.of("lock-key"));

            Job mockJob = mock(Job.class);
            when(jobService.createRagIndexJob(any(), isNull())).thenReturn(mockJob);

            VcsClient mockVcs = mock(VcsClient.class);
            when(vcsClientProvider.getClient(any())).thenReturn(mockVcs);
            when(mockVcs.getLatestCommitHash(anyString(), anyString(), anyString())).thenReturn("abc123");
            when(mockVcs.downloadRepositoryArchiveToFile(anyString(), anyString(), anyString(), any()))
                    .thenThrow(new java.io.IOException("Network error"));

            Map<String, Object> result = service.indexProjectFromVcs(createProjectDTO(100L), "main", messageConsumer);

            assertThat(result).containsEntry("status", "error");
            verify(ragIndexTrackingService).markIndexingFailed(eq(testProject), anyString());
            verify(jobService).failJob(eq(mockJob), anyString());
            verify(analysisLockService).releaseLock("lock-key");
        }

        @Test
        @DisplayName("should handle null commit hash")
        void shouldHandleNullCommitHash() throws Exception {
            setupProjectWithRagEnabled();
            setupProjectWithVcsBinding();
            setupProjectWorkspace();

            when(ragIndexingService.isAvailable()).thenReturn(true);
            when(projectRepository.findByIdWithFullDetails(100L)).thenReturn(Optional.of(testProject));
            when(ragIndexTrackingService.canStartIndexing(testProject)).thenReturn(true);
            when(analysisLockService.acquireLock(any(), anyString(), any())).thenReturn(Optional.of("lock-key"));

            Job mockJob = mock(Job.class);
            when(jobService.createRagIndexJob(any(), isNull())).thenReturn(mockJob);

            VcsClient mockVcs = mock(VcsClient.class);
            when(vcsClientProvider.getClient(any())).thenReturn(mockVcs);
            when(mockVcs.getLatestCommitHash(anyString(), anyString(), anyString())).thenReturn(null);

            Map<String, Object> result = service.indexProjectFromVcs(createProjectDTO(100L), "main", messageConsumer);

            assertThat(result).containsEntry("status", "error");
            verify(jobService).failJob(eq(mockJob), anyString());
            verify(analysisLockService).releaseLock("lock-key");
        }

        @Test
        @DisplayName("should use RAG config branch when request branch is null")
        void shouldUseRagConfigBranch() throws Exception {
            RagConfig ragConfig = new RagConfig(true, "develop");
            ProjectConfig config = new ProjectConfig(false, "develop", null, ragConfig);
            testProject.setConfiguration(config);
            setupProjectWithVcsBinding();
            setupProjectWorkspace();

            when(ragIndexingService.isAvailable()).thenReturn(true);
            when(projectRepository.findByIdWithFullDetails(100L)).thenReturn(Optional.of(testProject));
            when(ragIndexTrackingService.canStartIndexing(testProject)).thenReturn(true);
            when(analysisLockService.acquireLock(any(), eq("develop"), any())).thenReturn(Optional.of("lock-key"));

            Job mockJob = mock(Job.class);
            when(jobService.createRagIndexJob(any(), isNull())).thenReturn(mockJob);

            VcsClient mockVcs = mock(VcsClient.class);
            when(vcsClientProvider.getClient(any())).thenReturn(mockVcs);
            when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "develop")).thenReturn("c1");
            when(mockVcs.downloadRepositoryArchiveToFile(eq("my-workspace"), eq("my-repo"), eq("develop"), any()))
                    .thenReturn(512L);
            when(ragIndexingService.indexFromArchiveFile(any(), anyString(), anyString(), eq("develop"), eq("c1"), isNull()))
                    .thenReturn(Map.of("document_count", 10));

            Map<String, Object> result = service.indexProjectFromVcs(createProjectDTO(100L), null, messageConsumer);

            assertThat(result).containsEntry("status", "completed");
            assertThat(result).containsEntry("branch", "develop");
        }

        @Test
        @DisplayName("should use default branch when rag config branch is null")
        void shouldUseDefaultBranch() throws Exception {
            RagConfig ragConfig = new RagConfig(true);
            ProjectConfig config = new ProjectConfig(false, "master", null, ragConfig);
            testProject.setConfiguration(config);
            setupProjectWithVcsBinding();
            setupProjectWorkspace();

            when(ragIndexingService.isAvailable()).thenReturn(true);
            when(projectRepository.findByIdWithFullDetails(100L)).thenReturn(Optional.of(testProject));
            when(ragIndexTrackingService.canStartIndexing(testProject)).thenReturn(true);
            when(analysisLockService.acquireLock(any(), eq("master"), any())).thenReturn(Optional.of("lock-key"));

            Job mockJob = mock(Job.class);
            when(jobService.createRagIndexJob(any(), isNull())).thenReturn(mockJob);

            VcsClient mockVcs = mock(VcsClient.class);
            when(vcsClientProvider.getClient(any())).thenReturn(mockVcs);
            when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "master")).thenReturn("c1");
            when(mockVcs.downloadRepositoryArchiveToFile(eq("my-workspace"), eq("my-repo"), eq("master"), any()))
                    .thenReturn(100L);
            when(ragIndexingService.indexFromArchiveFile(any(), anyString(), anyString(), eq("master"), eq("c1"), isNull()))
                    .thenReturn(Map.of("document_count", 5));

            Map<String, Object> result = service.indexProjectFromVcs(createProjectDTO(100L), "", messageConsumer);

            assertThat(result).containsEntry("status", "completed");
            assertThat(result).containsEntry("branch", "master");
        }

        @Test
        @DisplayName("should apply exclude patterns from rag config")
        void shouldApplyExcludePatterns() throws Exception {
            java.util.List<String> excludePatterns = java.util.List.of("*.log", "vendor/**");
            RagConfig ragConfig = new RagConfig(true, "main", excludePatterns, false, 30);
            ProjectConfig config = new ProjectConfig(false, "main", null, ragConfig);
            testProject.setConfiguration(config);
            setupProjectWithVcsBinding();
            setupProjectWorkspace();

            when(ragIndexingService.isAvailable()).thenReturn(true);
            when(projectRepository.findByIdWithFullDetails(100L)).thenReturn(Optional.of(testProject));
            when(ragIndexTrackingService.canStartIndexing(testProject)).thenReturn(true);
            when(analysisLockService.acquireLock(any(), anyString(), any())).thenReturn(Optional.of("lock-key"));

            Job mockJob = mock(Job.class);
            when(jobService.createRagIndexJob(any(), isNull())).thenReturn(mockJob);

            VcsClient mockVcs = mock(VcsClient.class);
            when(vcsClientProvider.getClient(any())).thenReturn(mockVcs);
            when(mockVcs.getLatestCommitHash(anyString(), anyString(), anyString())).thenReturn("c1");
            when(mockVcs.downloadRepositoryArchiveToFile(anyString(), anyString(), anyString(), any()))
                    .thenReturn(1024L);
            when(ragIndexingService.indexFromArchiveFile(any(), anyString(), anyString(), anyString(), anyString(), eq(excludePatterns)))
                    .thenReturn(Map.of("document_count", 20));

            Map<String, Object> result = service.indexProjectFromVcs(createProjectDTO(100L), "main", messageConsumer);

            assertThat(result).containsEntry("status", "completed");
            verify(ragIndexingService).indexFromArchiveFile(any(), anyString(), anyString(), anyString(), anyString(), eq(excludePatterns));
        }

        @Test
        @DisplayName("should return error when no VCS connection")
        void shouldReturnErrorWhenNoVcsConnection() {
            setupProjectWithRagEnabled();
            // No VCS binding set
            when(ragIndexingService.isAvailable()).thenReturn(true);
            when(projectRepository.findByIdWithFullDetails(100L)).thenReturn(Optional.of(testProject));

            Map<String, Object> result = service.indexProjectFromVcs(createProjectDTO(100L), "main", messageConsumer);

            assertThat(result).containsEntry("status", "error");
            assertThat((String) result.get("message")).contains("VCS connection");
        }
    }
}

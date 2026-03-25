package org.rostilos.codecrow.ragengine.service;

import com.fasterxml.jackson.databind.ObjectMapper;
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
import org.rostilos.codecrow.queue.RedisQueueService;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.Map;
import java.util.Optional;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
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
    @Mock
    private RedisQueueService queueService;

    private VcsRagIndexingService service;

    private Project testProject;
    @Mock
    private Consumer<Map<String, Object>> messageConsumer;

    @BeforeEach
    void setUp() {
        ObjectMapper objectMapper = new ObjectMapper();
        // Spy is necessary here: we need the real indexProjectFromVcs() orchestration
        // to execute, while stubbing extractArchiveFileAndCleanup() and
        // pollRagIndexingJobAsync() which perform real I/O (disk, Redis polling).
        service = spy(new VcsRagIndexingService(
                projectRepository, vcsClientProvider, ragIndexingService,
                ragIndexTrackingService, analysisLockService, jobService,
                queueService, objectMapper));
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        ReflectionTestUtils.setField(service, "self", service); // Inject self

        testProject = new Project();
        ReflectionTestUtils.setField(testProject, "id", 100L);
        testProject.setName("test-project");
    }

    private ProjectDTO createProjectDTO(Long id) {
        return new ProjectDTO(id, null, null, false, null, null, null, null, null, null, null, null, null, null, null,
                null, null, null, null, null, null, null, null, null, null, null);
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

    @Nested
    @DisplayName("indexProjectFromVcs() - full indexing flow")
    class FullIndexingFlowTests {

        @Test
        @DisplayName("should complete full indexing successfully asynchronously")
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

            // Mock the private method to avoid attempting real unzip
            doNothing().when(service).extractArchiveFileAndCleanup(any(), any());
            doNothing().when(service).pollRagIndexingJobAsync(any(), any(), any(), any(), any(), any(), any(), any());

            Map<String, Object> result = service.indexProjectFromVcs(createProjectDTO(100L), "main", messageConsumer);

            assertThat(result).containsEntry("status", "queued");
            assertThat(result).containsEntry("branch", "main");
            verify(ragIndexTrackingService).markIndexingStarted(testProject, "main", "abc123");

            verify(queueService).leftPush(eq("codecrow:queue:rag"), anyString());
            verify(queueService).setExpiry(startsWith("codecrow:analysis:events:"), anyLong());

            // Polling should be called
            verify(service).pollRagIndexingJobAsync(anyString(), anyString(), eq(testProject), eq("main"), eq("abc123"),
                    any(), eq("lock-key"), eq(mockJob));
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
    }
}

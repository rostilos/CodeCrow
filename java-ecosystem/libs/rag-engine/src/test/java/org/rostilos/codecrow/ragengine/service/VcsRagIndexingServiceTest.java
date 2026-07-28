package org.rostilos.codecrow.ragengine.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.mockito.ArgumentCaptor;
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

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermission;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
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

    @Nested
    @DisplayName("archive-root normalization")
    class ArchiveRootNormalizationTests {

        @Test
        void shouldExposeRepositoryRelativePathsBeforePluginDetection(@TempDir Path destination)
                throws Exception {
            Path wrapper = Files.createDirectory(
                    destination.resolve("workspace-repository-0123456789abcdef"));
            Files.writeString(wrapper.resolve("composer.json"), "{\"require\":{}}");
            Files.createDirectories(wrapper.resolve("app/etc"));
            Files.writeString(wrapper.resolve("app/etc/config.php"), "<?php return [];");

            assertThat(VcsRagIndexingService.normalizeSingleArchiveRoot(destination)).isTrue();
            assertThat(destination.resolve("composer.json")).isRegularFile();
            assertThat(destination.resolve("app/etc/config.php")).isRegularFile();
            assertThat(wrapper).doesNotExist();
        }

        @Test
        void shouldLeaveAlreadyFlatOrAmbiguousArchiveUnchanged(@TempDir Path destination)
                throws Exception {
            Files.writeString(destination.resolve("composer.json"), "{\"require\":{}}");
            Files.createDirectories(destination.resolve("app/etc"));

            assertThat(VcsRagIndexingService.normalizeSingleArchiveRoot(destination)).isFalse();
            assertThat(destination.resolve("composer.json")).isRegularFile();
            assertThat(destination.resolve("app/etc")).isDirectory();
        }
    }

    @Nested
    @DisplayName("queued workspace ownership transfer")
    class WorkspaceOwnershipTransferTests {

        @Test
        void shouldAllowConsumerUidToRemoveDirectoriesWithoutBroadeningFileWrites(
                @TempDir Path destination) throws Exception {
            Path nested = Files.createDirectories(destination.resolve("app/etc"));
            Path source = Files.writeString(nested.resolve(".htaccess"), "deny from all");
            Files.setPosixFilePermissions(destination, Set.of(
                    PosixFilePermission.OWNER_READ,
                    PosixFilePermission.OWNER_WRITE,
                    PosixFilePermission.OWNER_EXECUTE));
            Files.setPosixFilePermissions(nested, Set.of(
                    PosixFilePermission.OWNER_READ,
                    PosixFilePermission.OWNER_WRITE,
                    PosixFilePermission.OWNER_EXECUTE));
            Files.setPosixFilePermissions(source, Set.of(
                    PosixFilePermission.OWNER_READ,
                    PosixFilePermission.OWNER_WRITE));

            VcsRagIndexingService.prepareTransferredWorkspacePermissions(destination);

            assertThat(Files.getPosixFilePermissions(destination)).contains(
                    PosixFilePermission.OTHERS_READ,
                    PosixFilePermission.OTHERS_WRITE,
                    PosixFilePermission.OTHERS_EXECUTE);
            assertThat(Files.getPosixFilePermissions(nested)).contains(
                    PosixFilePermission.OTHERS_READ,
                    PosixFilePermission.OTHERS_WRITE,
                    PosixFilePermission.OTHERS_EXECUTE);
            assertThat(Files.getPosixFilePermissions(source))
                    .contains(PosixFilePermission.OTHERS_READ)
                    .doesNotContain(PosixFilePermission.OTHERS_WRITE);
        }
    }

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
        ReflectionTestUtils.setField(service, "ragQueueInactivityTimeoutMinutes", 15L);
        ReflectionTestUtils.setField(service, "ragQueueLockLeaseMinutes", 30);
        ReflectionTestUtils.setField(service, "self", service); // Inject self

        testProject = new Project();
        ReflectionTestUtils.setField(testProject, "id", 100L);
        testProject.setName("test-project");
    }

    private ProjectDTO createProjectDTO(Long id) {
        return new ProjectDTO(id, null, null, false, null, null, null, null, null, null, null, null, null, null, null,
                null, null, null, null, null, null, null, null, null, null, null, null, null);
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

        @Test
        @DisplayName("should use repository default when RAG and project branches are absent")
        void shouldUseRepositoryDefaultBranch() {
            testProject.setConfiguration(new ProjectConfig(false, null, null, new RagConfig(true)));
            setupProjectWithVcsBinding();
            testProject.getVcsRepoBinding().setDefaultBranch("trunk");
            when(ragIndexingService.isAvailable()).thenReturn(true);
            when(projectRepository.findByIdWithFullDetails(100L)).thenReturn(Optional.of(testProject));
            when(ragIndexTrackingService.canStartIndexing(testProject)).thenReturn(true);
            when(analysisLockService.acquireLock(any(), eq("trunk"), any())).thenReturn(Optional.empty());

            Map<String, Object> result = service.indexProjectFromVcs(createProjectDTO(100L), null, messageConsumer);

            assertThat(result).containsEntry("status", "locked");
            verify(analysisLockService).acquireLock(any(), eq("trunk"), any());
        }

        @Test
        @DisplayName("should fail explicitly when no authoritative branch is configured")
        void shouldFailWhenNoAuthoritativeBranchIsConfigured() {
            testProject.setConfiguration(new ProjectConfig(false, null, null, new RagConfig(true)));
            setupProjectWithVcsBinding();
            when(ragIndexingService.isAvailable()).thenReturn(true);
            when(projectRepository.findByIdWithFullDetails(100L)).thenReturn(Optional.of(testProject));

            Map<String, Object> result = service.indexProjectFromVcs(createProjectDTO(100L), "  ", messageConsumer);

            assertThat(result)
                    .containsEntry("status", "error")
                    .containsEntry("message", "No RAG indexing branch is configured for project: test-project");
            verifyNoInteractions(ragIndexTrackingService, analysisLockService, jobService);
        }
    }

    private void setupProjectWithRagEnabled() {
        setupProjectWithRagEnabled(false);
    }

    private void setupProjectWithRagEnabled(boolean multiBranchEnabled) {
        RagConfig ragConfig = new RagConfig(
                true, "main", null, null, multiBranchEnabled, 90);
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

        @ParameterizedTest(name = "multi-branch enabled={0}")
        @ValueSource(booleans = {false, true})
        @DisplayName("should forward the project branch-retention mode")
        void shouldCompleteFullIndexing(boolean multiBranchEnabled) throws Exception {
            setupProjectWithRagEnabled(multiBranchEnabled);
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
            when(mockVcs.downloadRepositoryArchiveToFile(eq("my-workspace"), eq("my-repo"), eq("abc123"), any()))
                    .thenReturn(2048L);

            // Mock the private method to avoid attempting real unzip
            doNothing().when(service).extractArchiveFileAndCleanup(any(), any());
            doNothing().when(service).pollRagIndexingJobAsync(
                    any(), any(), any(), any(), any(), any(), any(), any(), any(), any());

            Map<String, Object> result = service.indexProjectFromVcs(createProjectDTO(100L), "main", messageConsumer);

            assertThat(result).containsEntry("status", "queued");
            assertThat(result).containsEntry("branch", "main");
            verify(ragIndexTrackingService).markIndexingStarted(testProject, "main", "abc123");
            verify(mockVcs).downloadRepositoryArchiveToFile(
                    eq("my-workspace"),
                    eq("my-repo"),
                    eq("abc123"),
                    any());

            ArgumentCaptor<String> queuedPayload = ArgumentCaptor.forClass(String.class);
            verify(queueService).leftPush(eq("codecrow:queue:rag"), queuedPayload.capture());
            assertThat(new ObjectMapper().readTree(queuedPayload.getValue())
                    .path("request")
                    .path("preserve_other_branches")
                    .asBoolean()).isEqualTo(multiBranchEnabled);
            assertThat(new ObjectMapper().readTree(queuedPayload.getValue())
                    .path("request")
                    .path("cleanup_repo_path")
                    .asBoolean()).isTrue();
            verify(queueService).setExpiry(startsWith("codecrow:analysis:events:"), anyLong());

            // Polling should be called
            verify(service).pollRagIndexingJobAsync(anyString(), anyString(), eq(testProject), eq("main"), eq("abc123"),
                    any(), eq("lock-key"), eq(mockJob), eq("codecrow:queue:rag"), eq(queuedPayload.getValue()));
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

    @Test
    @DisplayName("polling failure must not delete a consumer-owned workspace")
    void pollingFailureDoesNotDeleteConsumerOwnedWorkspace() throws Exception {
        Path consumerWorkspace = Files.createTempDirectory("codecrow-rag-test-");
        when(queueService.rightPop("events", 5))
                .thenReturn("{\"type\":\"error\",\"message\":\"worker failed\"}");
        when(analysisLockService.renewLock("lock-key", 30)).thenReturn(true);

        try {
            service.pollRagIndexingJobAsync(
                    "job-id",
                    "events",
                    testProject,
                    "main",
                    "abc123",
                    consumerWorkspace,
                    "lock-key",
                    null,
                    "codecrow:queue:rag",
                    "queued-payload");

            assertThat(consumerWorkspace).exists();
            verify(analysisLockService).releaseLock("lock-key");
            verify(analysisLockService).renewLock("lock-key", 30);
            verify(ragIndexTrackingService).markIndexingFailed(testProject, "worker failed");
        } finally {
            Files.deleteIfExists(consumerWorkspace);
        }
    }

    @Test
    @DisplayName("worker status heartbeats refresh the observable index status")
    void workerStatusHeartbeatRefreshesObservableIndexStatus() {
        when(queueService.rightPop("events", 5))
                .thenReturn(
                        "{\"type\":\"status\",\"state\":\"processing\"}",
                        "{\"type\":\"final\",\"result\":{\"document_count\":12,\"chunk_count\":34}}");
        when(analysisLockService.renewLock("lock-key", 30)).thenReturn(true);

        service.pollRagIndexingJobAsync(
                "job-id",
                "events",
                testProject,
                "main",
                "abc123",
                Path.of("/tmp/codecrow-rag-consumer-owned"),
                "lock-key",
                null,
                "codecrow:queue:rag",
                "queued-payload");

        verify(ragIndexTrackingService).markIndexingHeartbeat(testProject);
        verify(ragIndexTrackingService).markIndexingCompleted(
                testProject,
                "main",
                "abc123",
                12,
                34);
        verify(analysisLockService, times(2)).renewLock("lock-key", 30);
        verify(analysisLockService).releaseLock("lock-key");
    }
}

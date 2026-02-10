package org.rostilos.codecrow.ragengine.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.core.model.analysis.RagIndexStatus;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.RagConfig;
import org.rostilos.codecrow.core.model.rag.RagBranchIndex;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.rostilos.codecrow.core.service.AnalysisJobService;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class RagOperationsServiceImplTest {

    @Mock
    private RagIndexTrackingService ragIndexTrackingService;

    @Mock
    private IncrementalRagUpdateService incrementalRagUpdateService;

    @Mock
    private AnalysisLockService analysisLockService;

    @Mock
    private AnalysisJobService analysisJobService;

    @Mock
    private RagBranchIndexRepository ragBranchIndexRepository;

    @Mock
    private VcsClientProvider vcsClientProvider;

    @Mock
    private RagPipelineClient ragPipelineClient;

    private RagOperationsServiceImpl service;
    private Project testProject;

    @BeforeEach
    void setUp() {
        service = new RagOperationsServiceImpl(
                ragIndexTrackingService,
                incrementalRagUpdateService,
                analysisLockService,
                analysisJobService,
                ragBranchIndexRepository,
                vcsClientProvider,
                ragPipelineClient
        );
        
        testProject = new Project();
        ReflectionTestUtils.setField(testProject, "id", 100L);
    }

    @Test
    void testIsRagEnabled_ApiDisabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);

        boolean result = service.isRagEnabled(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void testIsRagEnabled_NullConfig() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        // testProject has no configuration set, so getConfiguration() returns null

        boolean result = service.isRagEnabled(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void testIsRagEnabled_RagDisabledInConfig() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        RagConfig ragConfig = new RagConfig(false);
        ProjectConfig config = new ProjectConfig(false, "main", null, ragConfig);
        testProject.setConfiguration(config);

        boolean result = service.isRagEnabled(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void testIsRagEnabled_Success() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        RagConfig ragConfig = new RagConfig(true);
        ProjectConfig config = new ProjectConfig(false, "main", null, ragConfig);
        testProject.setConfiguration(config);

        boolean result = service.isRagEnabled(testProject);

        assertThat(result).isTrue();
    }

    @Test
    void testIsRagIndexReady_RagNotEnabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);

        boolean result = service.isRagIndexReady(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void testIsRagIndexReady_ProjectNotIndexed() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        RagConfig ragConfig = new RagConfig(true);
        ProjectConfig config = new ProjectConfig(false, "main", null, ragConfig);
        testProject.setConfiguration(config);
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(false);

        boolean result = service.isRagIndexReady(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void testIsRagIndexReady_Success() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        RagConfig ragConfig = new RagConfig(true);
        ProjectConfig config = new ProjectConfig(false, "main", null, ragConfig);
        testProject.setConfiguration(config);
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);

        boolean result = service.isRagIndexReady(testProject);

        assertThat(result).isTrue();
    }

    @Test
    void testIsBranchIndexReady_True() {
        when(ragBranchIndexRepository.existsByProjectIdAndBranchName(100L, "feature")).thenReturn(true);

        boolean result = service.isBranchIndexReady(testProject, "feature");

        assertThat(result).isTrue();
        verify(ragBranchIndexRepository).existsByProjectIdAndBranchName(100L, "feature");
    }

    @Test
    void testIsBranchIndexReady_False() {
        when(ragBranchIndexRepository.existsByProjectIdAndBranchName(100L, "feature")).thenReturn(false);

        boolean result = service.isBranchIndexReady(testProject, "feature");

        assertThat(result).isFalse();
    }

    @Test
    void testTriggerIncrementalUpdate_WhenNotEnabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        service.triggerIncrementalUpdate(testProject, "main", "abc123", "diff", eventConsumer);

        verifyNoInteractions(analysisJobService);
        verifyNoInteractions(analysisLockService);
    }

    @Test
    void testCreateOrUpdateBranchIndex_WhenNotEnabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        service.createOrUpdateBranchIndex(testProject, "feature", "main", "commit123", "diff", eventConsumer);

        verifyNoInteractions(analysisJobService);
    }

    @Test
    void testEnsureRagIndexUpToDate_WhenNotEnabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureRagIndexUpToDate(testProject, "main", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testEnsureBranchIndexForPrTarget_WhenNotEnabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureBranchIndexForPrTarget(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testConstructor() {
        assertThat(service).isNotNull();
    }

    // ── Additional coverage tests ───────────────────────────────────────────

    @Test
    void testGetDeletedFilesForBranch_WhenExists() {
        RagBranchIndex index = new RagBranchIndex();
        index.setDeletedFiles(java.util.Set.of("old.java", "removed.java"));
        when(ragBranchIndexRepository.findByProjectIdAndBranchName(100L, "feature"))
                .thenReturn(Optional.of(index));

        java.util.Set<String> result = service.getDeletedFilesForBranch(testProject, "feature");

        assertThat(result).containsExactlyInAnyOrder("old.java", "removed.java");
    }

    @Test
    void testGetDeletedFilesForBranch_WhenNotExists() {
        when(ragBranchIndexRepository.findByProjectIdAndBranchName(100L, "unknown"))
                .thenReturn(Optional.empty());

        java.util.Set<String> result = service.getDeletedFilesForBranch(testProject, "unknown");

        assertThat(result).isEmpty();
    }

    @Test
    void testDeleteBranchIndex_WhenNotEnabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.deleteBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testDeleteBranchIndex_CannotDeleteMainBranch() {
        setupRagEnabled();
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.deleteBranchIndex(testProject, "main", eventConsumer);

        assertThat(result).isFalse();
        verify(eventConsumer).accept(argThat(m -> "warning".equals(m.get("type"))));
    }

    @Test
    void testDeleteBranchIndex_NoVcsBinding() {
        setupRagEnabled();
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.deleteBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testDeleteBranchIndex_Success() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragPipelineClient.deleteBranch("my-workspace", "my-repo", "feature")).thenReturn(true);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.deleteBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isTrue();
        verify(ragBranchIndexRepository).deleteByProjectIdAndBranchName(100L, "feature");
    }

    @Test
    void testDeleteBranchIndex_PipelineFailure() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragPipelineClient.deleteBranch("my-workspace", "my-repo", "feature")).thenReturn(false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.deleteBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
        verify(ragBranchIndexRepository, never()).deleteByProjectIdAndBranchName(anyLong(), anyString());
    }

    @Test
    void testDeleteBranchIndex_PipelineException() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragPipelineClient.deleteBranch("my-workspace", "my-repo", "feature"))
                .thenThrow(new RuntimeException("Connection timeout"));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.deleteBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testCleanupStaleBranches_WhenNotEnabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        Map<String, Object> result = service.cleanupStaleBranches(testProject, java.util.Set.of("feature"), eventConsumer);

        assertThat(result).containsEntry("status", "skipped");
    }

    @Test
    void testCleanupStaleBranches_NoVcsBinding() {
        setupRagEnabled();
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        Map<String, Object> result = service.cleanupStaleBranches(testProject, java.util.Set.of("feature"), eventConsumer);

        assertThat(result).containsEntry("status", "error");
    }

    @Test
    void testCleanupStaleBranches_NoStaleBranches() {
        setupRagEnabled();
        setupVcsBinding();
        when(ragPipelineClient.getIndexedBranches("my-workspace", "my-repo"))
                .thenReturn(java.util.List.of("main", "feature"));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        Map<String, Object> result = service.cleanupStaleBranches(testProject, java.util.Set.of("feature"), eventConsumer);

        assertThat(result).containsEntry("status", "success");
        assertThat(result).containsEntry("total_deleted", 0);
    }

    @Test
    void testCleanupStaleBranches_DeletesStaleBranch() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragPipelineClient.getIndexedBranches("my-workspace", "my-repo"))
                .thenReturn(java.util.List.of("main", "feature", "stale-branch"));
        when(ragPipelineClient.deleteBranch("my-workspace", "my-repo", "stale-branch")).thenReturn(true);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        Map<String, Object> result = service.cleanupStaleBranches(testProject, java.util.Set.of("feature"), eventConsumer);

        assertThat(result).containsEntry("status", "success");
        assertThat(result).containsEntry("total_deleted", 1);
    }

    @Test
    void testUpdateBranchIndex_WhenNotEnabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.updateBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testUpdateBranchIndex_IndexNotReady() {
        setupRagEnabled();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.updateBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testUpdateBranchIndex_NoVcsBinding() {
        setupRagEnabled();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.updateBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testUpdateBranchIndex_SameBranchAsBase() {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        // "main" is the base branch
        boolean result = service.updateBranchIndex(testProject, "main", eventConsumer);

        assertThat(result).isTrue();
    }

    @Test
    void testEnsureBranchIndexForPrTarget_NotEnabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureBranchIndexForPrTarget(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testEnsureBranchIndexForPrTarget_IndexNotReady() {
        setupRagEnabled();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureBranchIndexForPrTarget(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testEnsureBranchIndexForPrTarget_NoVcsBinding() {
        setupRagEnabled();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureBranchIndexForPrTarget(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testEnsureBranchIndexForPrTarget_SameBranch() {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureBranchIndexForPrTarget(testProject, "main", eventConsumer);

        assertThat(result).isTrue();
    }

    @Test
    void testEnsureRagIndexUpToDate_NoVcsBinding() {
        setupRagEnabled();
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureRagIndexUpToDate(testProject, "main", eventConsumer);

        assertThat(result).isFalse();
    }

    // ── triggerIncrementalUpdate full-flow tests ──────────────────────────

    @Test
    void testTriggerIncrementalUpdate_FullSuccessFlow() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);
        Job mockJob = mock(Job.class);

        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject)).thenReturn(true);
        when(incrementalRagUpdateService.parseDiffForRag("diff content"))
                .thenReturn(new IncrementalRagUpdateService.DiffResult(Set.of("src/A.java"), Set.of("src/B.java")));
        when(analysisJobService.createRagIndexJob(any(), anyBoolean(), any())).thenReturn(mockJob);
        when(analysisLockService.acquireLock(any(), eq("feature"), any(), eq("commit1"), isNull()))
                .thenReturn(Optional.of("lock-key"));
        when(incrementalRagUpdateService.performIncrementalUpdate(
                any(), any(), anyString(), anyString(), anyString(), anyString(), any(), any()))
                .thenReturn(Map.of("updatedFiles", 1, "deletedFiles", 1));
        when(ragBranchIndexRepository.findByProjectIdAndBranchName(100L, "feature"))
                .thenReturn(Optional.empty());

        service.triggerIncrementalUpdate(testProject, "feature", "commit1", "diff content", eventConsumer);

        verify(ragIndexTrackingService).markUpdatingStarted(testProject, "feature", "commit1");
        verify(ragIndexTrackingService).markUpdatingCompleted(testProject, "feature", "commit1");
        verify(analysisLockService).releaseLock("lock-key");
        verify(analysisJobService).completeJob(eq(mockJob), isNull());
        verify(ragBranchIndexRepository).save(any(RagBranchIndex.class));
    }

    @Test
    void testTriggerIncrementalUpdate_EmptyDiff_NoFilesChanged() {
        setupRagEnabled();
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject)).thenReturn(true);
        when(incrementalRagUpdateService.parseDiffForRag("empty"))
                .thenReturn(new IncrementalRagUpdateService.DiffResult(Set.of(), Set.of()));

        service.triggerIncrementalUpdate(testProject, "main", "abc", "empty", eventConsumer);

        verifyNoInteractions(analysisLockService);
    }

    @Test
    void testTriggerIncrementalUpdate_LockNotAcquired() {
        setupRagEnabled();
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);
        Job mockJob = mock(Job.class);

        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject)).thenReturn(true);
        when(incrementalRagUpdateService.parseDiffForRag(anyString()))
                .thenReturn(new IncrementalRagUpdateService.DiffResult(Set.of("a.java"), Set.of()));
        when(analysisJobService.createRagIndexJob(any(), anyBoolean(), any())).thenReturn(mockJob);
        when(analysisLockService.acquireLock(any(), anyString(), any(), anyString(), isNull()))
                .thenReturn(Optional.empty());

        service.triggerIncrementalUpdate(testProject, "feature", "c1", "diff", eventConsumer);

        verify(analysisJobService).failJob(eq(mockJob), anyString());
        verifyNoInteractions(ragIndexTrackingService);
    }

    @Test
    void testTriggerIncrementalUpdate_IncrementalUpdateThrows() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);
        Job mockJob = mock(Job.class);

        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject)).thenReturn(true);
        when(incrementalRagUpdateService.parseDiffForRag(anyString()))
                .thenReturn(new IncrementalRagUpdateService.DiffResult(Set.of("a.java"), Set.of()));
        when(analysisJobService.createRagIndexJob(any(), anyBoolean(), any())).thenReturn(mockJob);
        when(analysisLockService.acquireLock(any(), anyString(), any(), anyString(), isNull()))
                .thenReturn(Optional.of("lock-key"));
        when(incrementalRagUpdateService.performIncrementalUpdate(
                any(), any(), anyString(), anyString(), anyString(), anyString(), any(), any()))
                .thenThrow(new RuntimeException("Pipeline down"));

        service.triggerIncrementalUpdate(testProject, "feature", "c1", "diff", eventConsumer);

        verify(ragIndexTrackingService).markIndexingFailed(eq(testProject), anyString());
        verify(analysisLockService).releaseLock("lock-key");
    }

    @Test
    void testTriggerIncrementalUpdate_TrackBranchIndex_MergesDeletedFiles() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);
        Job mockJob = mock(Job.class);

        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject)).thenReturn(true);
        when(incrementalRagUpdateService.parseDiffForRag(anyString()))
                .thenReturn(new IncrementalRagUpdateService.DiffResult(Set.of(), Set.of("old.java")));
        when(analysisJobService.createRagIndexJob(any(), anyBoolean(), any())).thenReturn(mockJob);
        when(analysisLockService.acquireLock(any(), anyString(), any(), anyString(), isNull()))
                .thenReturn(Optional.of("lock-key"));
        when(incrementalRagUpdateService.performIncrementalUpdate(
                any(), any(), anyString(), anyString(), anyString(), anyString(), any(), any()))
                .thenReturn(Map.of("deletedFiles", 1));

        // Existing branch index with existing deleted files
        RagBranchIndex existingIndex = new RagBranchIndex();
        existingIndex.setDeletedFiles(new java.util.HashSet<>(Set.of("prev.java")));
        when(ragBranchIndexRepository.findByProjectIdAndBranchName(100L, "feature"))
                .thenReturn(Optional.of(existingIndex));

        service.triggerIncrementalUpdate(testProject, "feature", "c1", "diff", eventConsumer);

        verify(ragBranchIndexRepository).save(argThat(idx ->
                idx.getDeletedFiles().contains("old.java") && idx.getDeletedFiles().contains("prev.java")));
    }

    // ── updateBranchIndex full-flow tests ───────────────────────────────

    @Test
    void testUpdateBranchIndex_SuccessWithDiff() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        when(mockVcs.getBranchDiff("my-workspace", "my-repo", "main", "feature")).thenReturn("diff data");
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "feature")).thenReturn("abc123");
        // triggerIncrementalUpdate will be called but shouldPerform returns false
        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject)).thenReturn(false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.updateBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isTrue();
        verify(mockVcs).getBranchDiff("my-workspace", "my-repo", "main", "feature");
    }

    @Test
    void testUpdateBranchIndex_EmptyDiff() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        when(mockVcs.getBranchDiff("my-workspace", "my-repo", "main", "feature")).thenReturn("");
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.updateBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isTrue();
        verify(eventConsumer).accept(argThat(m -> "info".equals(m.get("type"))));
    }

    @Test
    void testUpdateBranchIndex_VcsClientException() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenThrow(new RuntimeException("VCS down"));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.updateBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
        verify(eventConsumer).accept(argThat(m -> "error".equals(m.get("type"))));
    }

    // ── ensureBranchIndexForPrTarget full-flow tests ────────────────────

    @Test
    void testEnsureBranchIndexForPrTarget_SuccessWithDiff() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        when(mockVcs.getBranchDiff("my-workspace", "my-repo", "main", "feature")).thenReturn("diff data");
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "feature")).thenReturn("xyz789");
        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject)).thenReturn(false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureBranchIndexForPrTarget(testProject, "feature", eventConsumer);

        assertThat(result).isTrue();
    }

    @Test
    void testEnsureBranchIndexForPrTarget_EmptyDiff() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        when(mockVcs.getBranchDiff("my-workspace", "my-repo", "main", "feature")).thenReturn(null);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureBranchIndexForPrTarget(testProject, "feature", eventConsumer);

        assertThat(result).isTrue();
    }

    @Test
    void testEnsureBranchIndexForPrTarget_VcsException() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        when(vcsClientProvider.getClient(any(VcsConnection.class)))
                .thenThrow(new RuntimeException("Connection refused"));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureBranchIndexForPrTarget(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
    }

    // ── ensureRagIndexUpToDate full-flow tests ──────────────────────────

    @Test
    void testEnsureRagIndexUpToDate_MainBranch_UpToDate() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "main")).thenReturn("same-commit");
        RagIndexStatus status = mock(RagIndexStatus.class);
        when(status.getIndexedCommitHash()).thenReturn("same-commit");
        when(ragIndexTrackingService.getIndexStatus(testProject)).thenReturn(Optional.of(status));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureRagIndexUpToDate(testProject, "main", eventConsumer);

        assertThat(result).isTrue();
        verify(mockVcs, never()).getBranchDiff(anyString(), anyString(), anyString(), anyString());
    }

    @Test
    void testEnsureRagIndexUpToDate_MainBranch_Outdated() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "main")).thenReturn("new-commit");
        RagIndexStatus status = mock(RagIndexStatus.class);
        when(status.getIndexedCommitHash()).thenReturn("old-commit");
        when(ragIndexTrackingService.getIndexStatus(testProject)).thenReturn(Optional.of(status));
        when(mockVcs.getBranchDiff("my-workspace", "my-repo", "old-commit", "new-commit")).thenReturn("diff");
        // triggerIncrementalUpdate is called internally but shouldPerform returns false
        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject)).thenReturn(false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        service.ensureRagIndexUpToDate(testProject, "main", eventConsumer);

        verify(mockVcs).getBranchDiff("my-workspace", "my-repo", "old-commit", "new-commit");
    }

    @Test
    void testEnsureRagIndexUpToDate_MainBranch_NullDiff() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "main")).thenReturn("new-commit");
        RagIndexStatus status = mock(RagIndexStatus.class);
        when(status.getIndexedCommitHash()).thenReturn("old-commit");
        when(ragIndexTrackingService.getIndexStatus(testProject)).thenReturn(Optional.of(status));
        when(mockVcs.getBranchDiff("my-workspace", "my-repo", "old-commit", "new-commit")).thenReturn(null);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureRagIndexUpToDate(testProject, "main", eventConsumer);

        assertThat(result).isTrue();
        verify(ragIndexTrackingService).markUpdatingCompleted(testProject, "main", "new-commit");
    }

    @Test
    void testEnsureRagIndexUpToDate_MainBranch_NoIndexStatus() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "main")).thenReturn("commit");
        when(ragIndexTrackingService.getIndexStatus(testProject)).thenReturn(Optional.empty());
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureRagIndexUpToDate(testProject, "main", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testEnsureRagIndexUpToDate_DifferentBranch_NoBranchIndex() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        // ensureMainIndexUpToDate
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "main")).thenReturn("main-commit");
        RagIndexStatus status = mock(RagIndexStatus.class);
        when(status.getIndexedCommitHash()).thenReturn("main-commit");
        when(ragIndexTrackingService.getIndexStatus(testProject)).thenReturn(Optional.of(status));
        // ensureBranchIndexUpToDate - no branch index exists
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "feature")).thenReturn("feat-commit");
        when(ragBranchIndexRepository.findByProjectIdAndBranchName(100L, "feature"))
                .thenReturn(Optional.empty());
        // ensureBranchIndexForPrTarget called
        when(ragBranchIndexRepository.existsByProjectIdAndBranchName(100L, "feature")).thenReturn(false);
        when(mockVcs.getBranchDiff("my-workspace", "my-repo", "main", "feature")).thenReturn("branch diff");
        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject)).thenReturn(false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureRagIndexUpToDate(testProject, "feature", eventConsumer);

        assertThat(result).isTrue();
    }

    @Test
    void testEnsureRagIndexUpToDate_DifferentBranch_UpToDate() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        // ensureMainIndexUpToDate
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "main")).thenReturn("m1");
        RagIndexStatus status = mock(RagIndexStatus.class);
        when(status.getIndexedCommitHash()).thenReturn("m1");
        when(ragIndexTrackingService.getIndexStatus(testProject)).thenReturn(Optional.of(status));
        // ensureBranchIndexUpToDate - branch index exists and up-to-date
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "feature")).thenReturn("f1");
        RagBranchIndex branchIndex = new RagBranchIndex();
        branchIndex.setCommitHash("f1");
        when(ragBranchIndexRepository.findByProjectIdAndBranchName(100L, "feature"))
                .thenReturn(Optional.of(branchIndex));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureRagIndexUpToDate(testProject, "feature", eventConsumer);

        assertThat(result).isTrue();
    }

    @Test
    void testEnsureRagIndexUpToDate_DifferentBranch_Outdated() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        // ensureMainIndexUpToDate
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "main")).thenReturn("m1");
        RagIndexStatus status = mock(RagIndexStatus.class);
        when(status.getIndexedCommitHash()).thenReturn("m1");
        when(ragIndexTrackingService.getIndexStatus(testProject)).thenReturn(Optional.of(status));
        // ensureBranchIndexUpToDate - branch exists but outdated
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "feature")).thenReturn("f2");
        RagBranchIndex branchIndex = new RagBranchIndex();
        branchIndex.setCommitHash("f1");
        when(ragBranchIndexRepository.findByProjectIdAndBranchName(100L, "feature"))
                .thenReturn(Optional.of(branchIndex));
        when(mockVcs.getBranchDiff("my-workspace", "my-repo", "f1", "f2")).thenReturn("incremental diff");
        // triggerIncrementalUpdate called internally - shouldPerform returns false so it exits early
        lenient().when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject)).thenReturn(false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureRagIndexUpToDate(testProject, "feature", eventConsumer);

        assertThat(result).isTrue();
    }

    @Test
    void testEnsureRagIndexUpToDate_DifferentBranch_NullDiff() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        // ensureMainIndexUpToDate
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "main")).thenReturn("m1");
        RagIndexStatus status = mock(RagIndexStatus.class);
        when(status.getIndexedCommitHash()).thenReturn("m1");
        when(ragIndexTrackingService.getIndexStatus(testProject)).thenReturn(Optional.of(status));
        // ensureBranchIndexUpToDate - branch outdated but null diff
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "feature")).thenReturn("f2");
        RagBranchIndex branchIndex = new RagBranchIndex();
        branchIndex.setCommitHash("f1");
        when(ragBranchIndexRepository.findByProjectIdAndBranchName(100L, "feature"))
                .thenReturn(Optional.of(branchIndex));
        when(mockVcs.getBranchDiff("my-workspace", "my-repo", "f1", "f2")).thenReturn(null);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureRagIndexUpToDate(testProject, "feature", eventConsumer);

        assertThat(result).isTrue();
        // Verify that getBranchDiff was called with the commit hashes
        verify(mockVcs).getBranchDiff("my-workspace", "my-repo", "f1", "f2");
    }

    @Test
    void testEnsureRagIndexUpToDate_Exception() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        when(vcsClientProvider.getClient(any(VcsConnection.class)))
                .thenThrow(new RuntimeException("VCS error"));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureRagIndexUpToDate(testProject, "main", eventConsumer);

        // Falls back to isRagIndexReady
        assertThat(result).isTrue();
    }

    // ── cleanupStaleBranches additional tests ───────────────────────────

    @Test
    void testCleanupStaleBranches_PipelineException() {
        setupRagEnabled();
        setupVcsBinding();
        when(ragPipelineClient.getIndexedBranches("my-workspace", "my-repo"))
                .thenThrow(new RuntimeException("Pipeline down"));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        Map<String, Object> result = service.cleanupStaleBranches(testProject, Set.of("feature"), eventConsumer);

        assertThat(result).containsEntry("status", "error");
    }

    @Test
    void testCleanupStaleBranches_PartialFailure() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragPipelineClient.getIndexedBranches("my-workspace", "my-repo"))
                .thenReturn(List.of("main", "stale1", "stale2"));
        when(ragPipelineClient.deleteBranch("my-workspace", "my-repo", "stale1")).thenReturn(true);
        when(ragPipelineClient.deleteBranch("my-workspace", "my-repo", "stale2")).thenReturn(false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        Map<String, Object> result = service.cleanupStaleBranches(testProject, Set.of(), eventConsumer);

        assertThat(result).containsEntry("status", "success");
        assertThat(result).containsEntry("total_deleted", 1);
        @SuppressWarnings("unchecked")
        List<String> failed = (List<String>) result.get("failed_branches");
        assertThat(failed).contains("stale2");
    }

    @Test
    void testCleanupStaleBranches_DeleteThrows() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragPipelineClient.getIndexedBranches("my-workspace", "my-repo"))
                .thenReturn(List.of("main", "stale1"));
        when(ragPipelineClient.deleteBranch("my-workspace", "my-repo", "stale1"))
                .thenThrow(new RuntimeException("Connection error"));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        Map<String, Object> result = service.cleanupStaleBranches(testProject, Set.of(), eventConsumer);

        assertThat(result).containsEntry("status", "success");
        @SuppressWarnings("unchecked")
        List<String> failed = (List<String>) result.get("failed_branches");
        assertThat(failed).contains("stale1");
    }

    // ── Helper methods ──────────────────────────────────────────────────────

    private void setupRagEnabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        RagConfig ragConfig = new RagConfig(true, "main");
        ProjectConfig config = new ProjectConfig(false, "main", null, ragConfig);
        testProject.setConfiguration(config);
    }

    private void setupVcsBinding() {
        VcsRepoBinding binding = new VcsRepoBinding();
        VcsConnection connection = new VcsConnection();
        binding.setVcsConnection(connection);
        binding.setExternalNamespace("my-workspace");
        binding.setExternalRepoSlug("my-repo");
        testProject.setVcsRepoBinding(binding);
    }
}

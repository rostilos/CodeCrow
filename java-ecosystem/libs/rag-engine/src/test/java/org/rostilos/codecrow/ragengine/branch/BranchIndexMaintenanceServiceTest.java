package org.rostilos.codecrow.ragengine.branch;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.RagConfig;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexKind;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.service.AnalysisJobService;
import org.rostilos.codecrow.ragengine.service.RagIndexTrackingService;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class BranchIndexMaintenanceServiceTest {

    @Test
    void unsetOptionalPatternsUseEmptyFiltersForInitialSnapshot() throws Exception {
        RagOperationsService ragOperations = mock(RagOperationsService.class);
        VcsClientProvider vcsClients = mock(VcsClientProvider.class);
        BranchIndexGenerationBuildService builds = mock(
                BranchIndexGenerationBuildService.class);
        BranchIndexBuildAdmissionService admissions = mock(
                BranchIndexBuildAdmissionService.class);
        RagIndexTrackingService tracking = mock(RagIndexTrackingService.class);
        AnalysisLockService locks = mock(AnalysisLockService.class);
        AnalysisJobService jobs = mock(AnalysisJobService.class);
        BranchIndexMaintenanceService service = new BranchIndexMaintenanceService(
                ragOperations, vcsClients, builds, admissions, tracking, locks, jobs,
                Runnable::run, 1);

        Project project = mock(Project.class);
        when(project.getId()).thenReturn(42L);
        when(project.getConfiguration()).thenReturn(new ProjectConfig(
                false, "main", null, new RagConfig(true, "main")));
        VcsRepoBinding binding = mock(VcsRepoBinding.class);
        VcsConnection connection = new VcsConnection();
        when(project.getVcsRepoBinding()).thenReturn(binding);
        when(binding.getVcsConnection()).thenReturn(connection);
        when(binding.getExternalNamespace()).thenReturn("workspace");
        when(binding.getExternalRepoSlug()).thenReturn("repository");
        VcsClient vcs = mock(VcsClient.class);
        when(vcsClients.getClient(connection)).thenReturn(vcs);
        when(vcs.getLatestCommitHash("workspace", "repository", "main"))
                .thenReturn("revision-a");
        when(ragOperations.getBaseBranch(project)).thenReturn("main");
        when(locks.acquireLock(eq(project), eq("main"), any(), eq("revision-a"), isNull()))
                .thenReturn(Optional.of("rag-lock"));

        Job job = mock(Job.class);
        when(job.getId()).thenReturn(91L);
        var prepared = new BranchIndexGenerationBuildService.PreparedBuild(
                81L, "physical-generation", false, null, "rag-lock");
        when(admissions.admit(
                eq(project), eq("main"), eq("revision-a"),
                eq(RagBranchIndexKind.PRIMARY), any(), eq("rag-lock"), any()))
                .thenReturn(new BranchIndexBuildAdmissionService.AdmittedBuild(
                        job, prepared,
                        BranchIndexBuildAdmissionService.ProjectStatusAdmission.INDEXING));
        when(builds.execute(
                eq(project), eq(connection), eq("workspace"), eq("repository"),
                eq("main"), eq("revision-a"), eq(RagBranchIndexKind.PRIMARY),
                eq(List.of()), eq(List.of()), eq(prepared), any()))
                .thenReturn(Map.of("document_count", 12, "chunk_count", 34));

        Map<String, Object> outcome = service.rebuild(
                project, "main", false, ignored -> { });

        assertThat(outcome.get("branches")).isEqualTo(List.of("main"));
        assertThat(outcome.get("failedBranches")).isEqualTo(Map.of());
        verify(builds).execute(
                eq(project), eq(connection), eq("workspace"), eq("repository"),
                eq("main"), eq("revision-a"), eq(RagBranchIndexKind.PRIMARY),
                eq(List.of()), eq(List.of()), eq(prepared), any());
        verify(tracking).reconcilePublishedGeneration(
                project, "main", "revision-a", 12, 34, 91L);
        verify(jobs).completeJob(job, Map.of("branch", "main", "revision", "revision-a"));
        verify(jobs, never()).failJob(any(), anyString());
        verify(locks).releaseLock("rag-lock");
    }

    @Test
    void observerAndLockCleanupFailuresCannotReverseAPublishedBuild() throws Exception {
        RagOperationsService ragOperations = mock(RagOperationsService.class);
        VcsClientProvider vcsClients = mock(VcsClientProvider.class);
        BranchIndexGenerationBuildService builds = mock(
                BranchIndexGenerationBuildService.class);
        BranchIndexBuildAdmissionService admissions = mock(
                BranchIndexBuildAdmissionService.class);
        RagIndexTrackingService tracking = mock(RagIndexTrackingService.class);
        AnalysisLockService locks = mock(AnalysisLockService.class);
        AnalysisJobService jobs = mock(AnalysisJobService.class);
        BranchIndexMaintenanceService service = new BranchIndexMaintenanceService(
                ragOperations, vcsClients, builds, admissions, tracking, locks, jobs,
                Runnable::run, 1);

        Project project = mock(Project.class);
        when(project.getId()).thenReturn(42L);
        when(project.getConfiguration()).thenReturn(new ProjectConfig(
                false, "main", null,
                new RagConfig(true, "main", List.of(), List.of())));
        VcsRepoBinding binding = mock(VcsRepoBinding.class);
        VcsConnection connection = new VcsConnection();
        when(project.getVcsRepoBinding()).thenReturn(binding);
        when(binding.getVcsConnection()).thenReturn(connection);
        when(binding.getExternalNamespace()).thenReturn("workspace");
        when(binding.getExternalRepoSlug()).thenReturn("repository");
        VcsClient vcs = mock(VcsClient.class);
        when(vcsClients.getClient(connection)).thenReturn(vcs);
        when(vcs.getLatestCommitHash("workspace", "repository", "main"))
                .thenReturn("revision-a");
        when(ragOperations.getBaseBranch(project)).thenReturn("main");
        when(locks.acquireLock(eq(project), eq("main"), any(), eq("revision-a"), isNull()))
                .thenReturn(Optional.of("rag-lock"));

        Job job = mock(Job.class);
        when(job.getId()).thenReturn(91L);
        var prepared = new BranchIndexGenerationBuildService.PreparedBuild(
                81L, "physical-generation", false, null, "rag-lock");
        when(admissions.admit(
                eq(project), eq("main"), eq("revision-a"),
                eq(RagBranchIndexKind.PRIMARY), any(), eq("rag-lock"), any()))
                .thenReturn(new BranchIndexBuildAdmissionService.AdmittedBuild(
                        job, prepared,
                        BranchIndexBuildAdmissionService.ProjectStatusAdmission.INDEXING));
        when(builds.execute(
                eq(project), eq(connection), eq("workspace"), eq("repository"),
                eq("main"), eq("revision-a"), eq(RagBranchIndexKind.PRIMARY),
                eq(List.of()), eq(List.of()), eq(prepared), any()))
                .thenAnswer(invocation -> {
                    @SuppressWarnings("unchecked")
                    Consumer<Map<String, Object>> progress = invocation.getArgument(10);
                    progress.accept(Map.of("stage", "indexing", "message", "halfway"));
                    return Map.of("document_count", 12, "chunk_count", 34);
                });
        doThrow(new IllegalStateException("lock database unavailable"))
                .when(locks).releaseLock("rag-lock");

        Map<String, Object> outcome = service.rebuild(
                project,
                "main",
                false,
                ignored -> {
                    throw new IllegalStateException("observer disconnected");
                });

        assertThat(outcome.get("branches")).isEqualTo(List.of("main"));
        assertThat(outcome.get("failedBranches")).isEqualTo(Map.of());
        verify(tracking).reconcilePublishedGeneration(
                project, "main", "revision-a", 12, 34, 91L);
        verify(jobs).completeJob(job, Map.of("branch", "main", "revision", "revision-a"));
        verify(jobs, never()).failJob(any(), anyString());
        verify(tracking, never()).markIndexingFailed(any(), anyString(), any());
        verify(locks).releaseLock("rag-lock");
    }
}

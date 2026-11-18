package org.rostilos.codecrow.pipelineagent.generic.service;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexGenerationStatus;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexGenerationRepository;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;

import java.util.List;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class RagGenerationCleanupServiceTest {
    private final RagBranchIndexGenerationRepository generations = mock(RagBranchIndexGenerationRepository.class);
    private final ProjectRepository projects = mock(ProjectRepository.class);
    private final RagPipelineClient pipeline = mock(RagPipelineClient.class);
    private final RagGenerationCleanupService cleanup = new RagGenerationCleanupService(generations, projects, pipeline);

    @Test
    void retriesRetiringGenerationAfterReaderReleasesIt() {
        var candidate = mock(RagBranchIndexGenerationRepository.RetirementCandidate.class);
        when(candidate.getGenerationId()).thenReturn(1L);
        when(candidate.getStatus()).thenReturn(
                RagBranchIndexGenerationStatus.SUPERSEDED, RagBranchIndexGenerationStatus.RETIRING);
        when(candidate.getWorkspaceName()).thenReturn("workspace");
        when(candidate.getProjectNamespace()).thenReturn("project");
        when(candidate.getBranchName()).thenReturn("main");
        when(candidate.getCollectionName()).thenReturn("target");
        when(candidate.getRevision()).thenReturn("revision");
        when(candidate.getManifestDigest()).thenReturn("manifest");
        when(generations.findRetirementCandidates(any())).thenReturn(List.of(candidate));
        when(generations.beginRetirement(eq(1L), any())).thenReturn(1);
        when(pipeline.isEnabled()).thenReturn(true);
        when(pipeline.deleteBranchWithOutcome("workspace", "project", "main", "target", "revision", "manifest"))
                .thenReturn(RagPipelineClient.BranchDeletionOutcome.failure(
                        "target", RagPipelineClient.BranchDeletionFailure.TARGET, 423, "active reader"))
                .thenReturn(RagPipelineClient.BranchDeletionOutcome.success("target"));

        cleanup.cleanup();
        verify(generations, never()).finishRetirement(anyLong());

        cleanup.cleanup();
        verify(generations).beginRetirement(eq(1L), any());
        verify(generations).finishRetirement(1L);
    }

    @Test
    void onlyReclaimsUnregisteredTargetsOfDeletedProjects() throws Exception {
        when(pipeline.isEnabled()).thenReturn(true);
        when(pipeline.listOrphanCandidates()).thenReturn(List.of(
                orphan(1L, "live-project"),
                orphan(2L, "registered-target"),
                orphan(3L, "orphan")));
        when(projects.existsById(1L)).thenReturn(true);
        when(generations.existsByCollectionName("registered-target")).thenReturn(true);
        when(pipeline.deleteBranchWithOutcome("workspace", "project", "main", "orphan", "revision", "manifest"))
                .thenReturn(RagPipelineClient.BranchDeletionOutcome.success("orphan"));

        cleanup.cleanup();

        verify(pipeline).deleteBranchWithOutcome("workspace", "project", "main", "orphan", "revision", "manifest");
        verify(pipeline, times(1)).deleteBranchWithOutcome(anyString(), anyString(), anyString(),
                anyString(), anyString(), anyString());
    }

    private static RagPipelineClient.OrphanCandidate orphan(long projectId, String target) {
        return new RagPipelineClient.OrphanCandidate(projectId, "workspace", "project", "main",
                target, "revision", "manifest");
    }
}

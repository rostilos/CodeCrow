package org.rostilos.codecrow.ragengine.branch;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.RagConfig;
import org.rostilos.codecrow.core.model.rag.RagBranchIndex;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexGeneration;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexKind;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexGenerationRepository;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
import org.springframework.test.util.ReflectionTestUtils;

import java.time.OffsetDateTime;
import java.util.List;

import static org.mockito.Mockito.*;

class RagTransientBranchIndexCleanupServiceTest {

    @Test
    void deletesExpiredTransientGenerationUsingProjectTenantCoordinates() throws Exception {
        RagBranchIndexRepository branches = mock(RagBranchIndexRepository.class);
        RagBranchIndexGenerationRepository generations =
                mock(RagBranchIndexGenerationRepository.class);
        RagPipelineClient pipeline = mock(RagPipelineClient.class);
        Project project = new Project();
        ReflectionTestUtils.setField(project, "id", 42L);
        project.setNamespace("namespace");
        Workspace workspace = new Workspace();
        workspace.setName("workspace");
        project.setWorkspace(workspace);
        project.setConfiguration(new ProjectConfig(
                false, "master", null,
                new RagConfig(true, "master", null, null,
                        true, 30, List.of("develop"), true)));
        RagBranchIndex index = new RagBranchIndex(
                project, "release/candidate", RagBranchIndexKind.TRANSIENT);
        index.setId(10L);
        index.setLastAccessedAt(OffsetDateTime.now().minusDays(31));
        RagBranchIndexGeneration generation = mock(RagBranchIndexGeneration.class);
        when(generation.getCollectionName()).thenReturn("opaque-transient-target");
        when(branches.findByIndexKind(RagBranchIndexKind.TRANSIENT))
                .thenReturn(List.of(index));
        when(generations.findByBranchIndexIdOrderByCreatedAtDesc(10L))
                .thenReturn(List.of(generation));
        when(pipeline.deleteBranch(
                "workspace", "namespace", "release/candidate",
                "opaque-transient-target"))
                .thenReturn(true);

        new RagTransientBranchIndexCleanupService(
                branches, generations, pipeline).cleanupExpired();

        verify(branches).delete(index);
    }
}

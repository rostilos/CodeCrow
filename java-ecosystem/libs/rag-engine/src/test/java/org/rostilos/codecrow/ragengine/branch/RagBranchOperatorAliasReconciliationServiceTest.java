package org.rostilos.codecrow.ragengine.branch;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.rag.RagBranchIndex;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexGeneration;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexKind;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;

import java.util.List;

import static org.mockito.Mockito.*;

class RagBranchOperatorAliasReconciliationServiceTest {

    @Test
    void restoresReadableAliasesForDurableAndPrimaryGenerationsOnly() throws Exception {
        RagBranchIndexRepository repository = mock(RagBranchIndexRepository.class);
        RagPipelineClient client = mock(RagPipelineClient.class);
        RagBranchOperatorAliasReconciliationService service =
                new RagBranchOperatorAliasReconciliationService(repository, client);

        RagBranchIndex primary = index("main", RagBranchIndexKind.PRIMARY, "main-target");
        RagBranchIndex durable = index("develop", RagBranchIndexKind.DURABLE, "develop-target");
        RagBranchIndex transientIndex = index("release", RagBranchIndexKind.TRANSIENT, "release-target");
        when(repository.findAll()).thenReturn(List.of(primary, durable, transientIndex));

        service.reconcileActiveGenerationAliases();

        verify(client).publishGenerationAliases(
                "workspace", "project", "main", "revision", "main-target", true, true);
        verify(client).publishGenerationAliases(
                "workspace", "project", "develop", "revision", "develop-target", true, false);
        verifyNoMoreInteractions(client);
    }

    private static RagBranchIndex index(
            String branch,
            RagBranchIndexKind kind,
            String target) {
        Workspace workspace = mock(Workspace.class);
        when(workspace.getName()).thenReturn("workspace");
        Project project = mock(Project.class);
        when(project.getWorkspace()).thenReturn(workspace);
        when(project.getNamespace()).thenReturn("project");
        when(project.getId()).thenReturn(1L);
        RagBranchIndexGeneration generation = mock(RagBranchIndexGeneration.class);
        when(generation.getRevision()).thenReturn("revision");
        when(generation.getCollectionName()).thenReturn(target);
        RagBranchIndex index = mock(RagBranchIndex.class);
        when(index.getProject()).thenReturn(project);
        when(index.getBranchName()).thenReturn(branch);
        when(index.getIndexKind()).thenReturn(kind);
        when(index.getActiveGeneration()).thenReturn(generation);
        return index;
    }
}

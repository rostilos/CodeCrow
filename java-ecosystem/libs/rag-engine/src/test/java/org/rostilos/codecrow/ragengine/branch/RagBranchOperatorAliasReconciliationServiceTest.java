package org.rostilos.codecrow.ragengine.branch;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexKind;
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

        var primary = candidate("main", RagBranchIndexKind.PRIMARY, "main-target");
        var durable = candidate("develop", RagBranchIndexKind.DURABLE, "develop-target");
        when(repository.findOperatorAliasCandidates()).thenReturn(List.of(primary, durable));

        service.reconcileActiveGenerationAliases();

        verify(client).publishGenerationAliases(
                "workspace", "project", "main", "revision", "main-target", true, true);
        verify(client).publishGenerationAliases(
                "workspace", "project", "develop", "revision", "develop-target", true, false);
        verifyNoMoreInteractions(client);
    }

    private static RagBranchIndexRepository.OperatorAliasCandidate candidate(
            String branch,
            RagBranchIndexKind kind,
            String target) {
        var candidate = mock(RagBranchIndexRepository.OperatorAliasCandidate.class);
        when(candidate.getProjectId()).thenReturn(1L);
        when(candidate.getWorkspaceName()).thenReturn("workspace");
        when(candidate.getProjectNamespace()).thenReturn("project");
        when(candidate.getBranchName()).thenReturn(branch);
        when(candidate.getRevision()).thenReturn("revision");
        when(candidate.getCollectionName()).thenReturn(target);
        when(candidate.getIndexKind()).thenReturn(kind);
        return candidate;
    }
}

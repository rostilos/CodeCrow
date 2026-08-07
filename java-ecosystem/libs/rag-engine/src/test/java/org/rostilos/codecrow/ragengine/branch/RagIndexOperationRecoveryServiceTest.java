package org.rostilos.codecrow.ragengine.branch;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.rag.RagIndexOperation;
import org.rostilos.codecrow.ragengine.service.RagBranchIndexRegistryService;

import java.time.OffsetDateTime;
import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class RagIndexOperationRecoveryServiceTest {

    @Test
    void abandonedPersistedOperationBecomesTerminalWithUsefulDiagnostic() {
        RagBranchIndexRegistryService registry = mock(RagBranchIndexRegistryService.class);
        RagIndexOperation operation = new RagIndexOperation();
        operation.setId(81L);
        operation.setBranchName("develop");
        when(registry.findRecoverableOperations(any(OffsetDateTime.class)))
                .thenReturn(List.of(operation));

        new RagIndexOperationRecoveryService(registry, 30)
                .failAbandonedOperations();

        verify(registry).fail(eq(81L), argThat(message ->
                message.contains("stopped heartbeating")
                        && message.contains("previous active generation was preserved")));
    }
}

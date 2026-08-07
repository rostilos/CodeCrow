package org.rostilos.codecrow.ragengine.branch;

import org.rostilos.codecrow.ragengine.service.RagBranchIndexRegistryService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;

/**
 * Terminates registry operations whose producer disappeared before publishing
 * a generation. The prior active generation remains queryable; a later event
 * retries the same idempotency key from a fresh snapshot or checkpoint.
 */
@Service
public class RagIndexOperationRecoveryService {

    private static final Logger log = LoggerFactory.getLogger(
            RagIndexOperationRecoveryService.class);

    private final RagBranchIndexRegistryService registryService;
    private final long staleAfterMinutes;

    public RagIndexOperationRecoveryService(
            RagBranchIndexRegistryService registryService,
            @Value("${codecrow.rag.generation.stale-after-minutes:30}")
            long staleAfterMinutes) {
        this.registryService = registryService;
        this.staleAfterMinutes = Math.max(5, staleAfterMinutes);
    }

    @Scheduled(
            fixedDelayString = "${codecrow.rag.generation.recovery-interval-ms:300000}",
            initialDelayString = "${codecrow.rag.generation.recovery-initial-delay-ms:60000}")
    public void failAbandonedOperations() {
        OffsetDateTime cutoff = OffsetDateTime.now().minusMinutes(staleAfterMinutes);
        for (var operation : registryService.findRecoverableOperations(cutoff)) {
            String diagnostic = "Exact RAG generation producer stopped heartbeating for "
                    + staleAfterMinutes + " minutes; the previous active generation was preserved";
            try {
                registryService.fail(operation.getId(), diagnostic);
                log.warn("Failed abandoned RAG generation operation {} for branch {}",
                        operation.getId(), operation.getBranchName());
            } catch (Exception failure) {
                log.error("Could not terminalize abandoned RAG generation operation {}",
                        operation.getId(), failure);
            }
        }
    }
}

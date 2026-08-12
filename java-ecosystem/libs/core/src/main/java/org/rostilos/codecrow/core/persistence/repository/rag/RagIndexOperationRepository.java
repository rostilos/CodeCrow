package org.rostilos.codecrow.core.persistence.repository.rag;

import org.rostilos.codecrow.core.model.rag.RagIndexOperation;
import org.rostilos.codecrow.core.model.rag.RagIndexOperationStatus;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

@Repository
public interface RagIndexOperationRepository extends JpaRepository<RagIndexOperation, Long> {

    Optional<RagIndexOperation> findByProjectIdAndOperationKey(Long projectId, String operationKey);

    List<RagIndexOperation> findByStatusInAndUpdatedAtBefore(
            List<RagIndexOperationStatus> statuses,
            OffsetDateTime updatedBefore);
}

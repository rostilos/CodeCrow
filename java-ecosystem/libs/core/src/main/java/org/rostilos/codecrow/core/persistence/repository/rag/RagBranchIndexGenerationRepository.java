package org.rostilos.codecrow.core.persistence.repository.rag;

import org.rostilos.codecrow.core.model.rag.RagBranchIndexGeneration;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexGenerationStatus;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface RagBranchIndexGenerationRepository extends JpaRepository<RagBranchIndexGeneration, Long> {

    Optional<RagBranchIndexGeneration> findFirstByBranchIndexIdAndRevisionAndStatusInOrderByCreatedAtDesc(
            Long branchIndexId,
            String revision,
            List<RagBranchIndexGenerationStatus> statuses);

    List<RagBranchIndexGeneration> findByBranchIndexIdOrderByCreatedAtDesc(Long branchIndexId);

    @Query("SELECT g FROM RagBranchIndexGeneration g "
            + "JOIN g.branchIndex b WHERE b.project.id = :projectId "
            + "AND b.branchName = :branchName AND g.revision = :revision "
            + "AND g.status IN :statuses ORDER BY g.createdAt DESC")
    List<RagBranchIndexGeneration> findAvailableExactGeneration(
            @Param("projectId") Long projectId,
            @Param("branchName") String branchName,
            @Param("revision") String revision,
            @Param("statuses") List<RagBranchIndexGenerationStatus> statuses);
}

package org.rostilos.codecrow.core.persistence.repository.rag;

import org.rostilos.codecrow.core.model.rag.RagBranchIndex;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

/**
 * Repository for RAG branch index tracking.
 */
@Repository
public interface RagBranchIndexRepository extends JpaRepository<RagBranchIndex, Long> {

    Optional<RagBranchIndex> findByProjectIdAndBranchName(Long projectId, String branchName);

    List<RagBranchIndex> findByProjectId(Long projectId);

    @Query("SELECT CASE WHEN COUNT(b) > 0 THEN true ELSE false END FROM RagBranchIndex b " +
           "WHERE b.project.id = :projectId AND b.branchName = :branchName")
    boolean existsByProjectIdAndBranchName(@Param("projectId") Long projectId, @Param("branchName") String branchName);

    @Modifying
    void deleteByProjectId(Long projectId);

    @Modifying
    void deleteByProjectIdAndBranchName(Long projectId, String branchName);

    @Query("SELECT b.branchName FROM RagBranchIndex b WHERE b.project.id = :projectId")
    List<String> findBranchNamesByProjectId(@Param("projectId") Long projectId);
}

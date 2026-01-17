package org.rostilos.codecrow.core.persistence.repository.qualitygate;

import org.rostilos.codecrow.core.model.qualitygate.QualityGate;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface QualityGateRepository extends JpaRepository<QualityGate, Long> {

    List<QualityGate> findByWorkspaceId(Long workspaceId);

    List<QualityGate> findByWorkspaceIdAndActiveTrue(Long workspaceId);

    Optional<QualityGate> findByWorkspaceIdAndIsDefaultTrue(Long workspaceId);

    Optional<QualityGate> findByWorkspaceIdAndName(Long workspaceId, String name);

    boolean existsByWorkspaceIdAndName(Long workspaceId, String name);

    @Query("SELECT qg FROM QualityGate qg LEFT JOIN FETCH qg.conditions WHERE qg.id = :id")
    Optional<QualityGate> findByIdWithConditions(@Param("id") Long id);

    @Query("SELECT qg FROM QualityGate qg LEFT JOIN FETCH qg.conditions WHERE qg.workspace.id = :workspaceId AND qg.isDefault = true")
    Optional<QualityGate> findDefaultWithConditions(@Param("workspaceId") Long workspaceId);

    Optional<QualityGate> findByIdAndWorkspaceId(Long id, Long workspaceId);

    @org.springframework.data.jpa.repository.Modifying
    @Query("UPDATE QualityGate qg SET qg.isDefault = false WHERE qg.workspace.id = :workspaceId")
    void clearDefaultForWorkspace(@Param("workspaceId") Long workspaceId);
}

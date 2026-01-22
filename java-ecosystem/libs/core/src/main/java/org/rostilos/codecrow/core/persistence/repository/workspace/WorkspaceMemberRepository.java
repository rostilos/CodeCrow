package org.rostilos.codecrow.core.persistence.repository.workspace;

import java.util.List;
import java.util.Optional;

import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.model.workspace.WorkspaceMember;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

@Repository
public interface WorkspaceMemberRepository extends JpaRepository<WorkspaceMember, Long> {
    Optional<WorkspaceMember> findByWorkspaceIdAndUserId(Long workspaceId, Long userId);
    java.util.List<WorkspaceMember> findByUser_Id(Long userId);
    java.util.List<WorkspaceMember> findByWorkspace_Id(Long workspaceId);
    Long countByWorkspace_Id(Long workspaceId);

    @Modifying
    @Query("DELETE FROM WorkspaceMember wm WHERE wm.workspace.id = :workspaceId")
    void deleteAllByWorkspaceId(@Param("workspaceId") Long workspaceId);

    @Query("SELECT wm.workspace FROM WorkspaceMember wm " +
            "WHERE wm.user.id = :userId AND wm.status = 'ACTIVE'")
    List<Workspace> findActiveWorkspacesByUserId(@Param("userId") Long userId);
}
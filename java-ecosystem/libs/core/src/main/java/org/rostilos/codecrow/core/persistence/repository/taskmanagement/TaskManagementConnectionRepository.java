package org.rostilos.codecrow.core.persistence.repository.taskmanagement;

import org.rostilos.codecrow.core.model.taskmanagement.ETaskManagementProvider;
import org.rostilos.codecrow.core.model.taskmanagement.TaskManagementConnection;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface TaskManagementConnectionRepository extends JpaRepository<TaskManagementConnection, Long> {

    List<TaskManagementConnection> findByWorkspaceId(Long workspaceId);

    List<TaskManagementConnection> findByWorkspaceIdAndProviderType(Long workspaceId, ETaskManagementProvider providerType);

    Optional<TaskManagementConnection> findByIdAndWorkspaceId(Long id, Long workspaceId);

    boolean existsByWorkspaceIdAndConnectionName(Long workspaceId, String connectionName);

    void deleteByIdAndWorkspaceId(Long id, Long workspaceId);
}

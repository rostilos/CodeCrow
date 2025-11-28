package org.rostilos.codecrow.core.persistence.repository.ai;

import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface AiConnectionRepository extends JpaRepository<AIConnection, Long> {
    List<AIConnection> findByWorkspace_Id(Long workspaceId);
    Optional<AIConnection> findByWorkspace_IdAndId(Long workspaceId, Long connectionId);
    Optional<AIConnection> findByWorkspaceIdAndProviderKey(Long workspaceId, AIProviderKey providerKey);
    boolean existsByWorkspaceIdAndProviderKey(Long workspaceId, AIProviderKey providerKey);
}
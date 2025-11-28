package org.rostilos.codecrow.core.persistence.repository.vcs;

import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface VcsConnectionRepository extends JpaRepository<VcsConnection, Long> {
    List<VcsConnection> findByWorkspace_IdAndProviderType(Long workspaceId, EVcsProvider provider);

    Optional<VcsConnection> findByWorkspace_IdAndId(Long workspaceId, Long id);

    List<VcsConnection> findByWorkspace_Id(@Param("workspaceId") Long workspaceId);
}
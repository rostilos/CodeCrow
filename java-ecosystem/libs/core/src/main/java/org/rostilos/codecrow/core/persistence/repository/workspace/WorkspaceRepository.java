package org.rostilos.codecrow.core.persistence.repository.workspace;

import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

@Repository
public interface WorkspaceRepository extends JpaRepository<Workspace, Long> {
    Optional<Workspace> findBySlug(String slug);
    boolean existsBySlug(String slug);
    
    List<Workspace> findByScheduledDeletionAtBeforeAndScheduledDeletionAtIsNotNull(OffsetDateTime dateTime);
}

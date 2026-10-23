package org.rostilos.codecrow.core.persistence.repository.user;

import org.rostilos.codecrow.core.model.user.PersonalAccessToken;
import org.springframework.data.jpa.repository.EntityGraph;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;
import java.util.Optional;

public interface PersonalAccessTokenRepository extends JpaRepository<PersonalAccessToken, Long> {
    @EntityGraph(attributePaths = {"user", "workspace"})
    Optional<PersonalAccessToken> findByTokenHash(String tokenHash);
    List<PersonalAccessToken> findByUserIdAndWorkspaceIdOrderByCreatedAtDesc(Long userId, Long workspaceId);
    Optional<PersonalAccessToken> findByIdAndUserIdAndWorkspaceId(Long id, Long userId, Long workspaceId);
}

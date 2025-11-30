package org.rostilos.codecrow.core.persistence.repository.vcs;

import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

/**
 * Repository for VcsRepoBinding entity.
 * Provides methods to find repository bindings for webhook processing and project management.
 */
@Repository
public interface VcsRepoBindingRepository extends JpaRepository<VcsRepoBinding, Long> {

    Optional<VcsRepoBinding> findByProviderAndExternalRepoId(EVcsProvider provider, String externalRepoId);

    Optional<VcsRepoBinding> findByProject_Id(Long projectId);

    List<VcsRepoBinding> findByWorkspace_Id(Long workspaceId);

    List<VcsRepoBinding> findByVcsConnection_Id(Long vcsConnectionId);

    List<VcsRepoBinding> findByWorkspace_IdAndProvider(Long workspaceId, EVcsProvider provider);

    boolean existsByProviderAndExternalRepoId(EVcsProvider provider, String externalRepoId);

    @Query("SELECT b FROM VcsRepoBinding b " +
           "LEFT JOIN FETCH b.project " +
           "LEFT JOIN FETCH b.vcsConnection " +
           "WHERE b.provider = :provider AND b.externalRepoId = :externalRepoId")
    Optional<VcsRepoBinding> findByProviderAndExternalRepoIdWithDetails(
            @Param("provider") EVcsProvider provider,
            @Param("externalRepoId") String externalRepoId
    );

    @Query("SELECT b FROM VcsRepoBinding b " +
           "LEFT JOIN FETCH b.project " +
           "WHERE b.vcsConnection.id = :connectionId")
    List<VcsRepoBinding> findByVcsConnectionIdWithProject(@Param("connectionId") Long connectionId);
}

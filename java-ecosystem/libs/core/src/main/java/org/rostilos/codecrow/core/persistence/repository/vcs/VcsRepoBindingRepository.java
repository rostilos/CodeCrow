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


    Optional<VcsRepoBinding> findByProject_Id(Long projectId);

    List<VcsRepoBinding> findByWorkspace_Id(Long workspaceId);

    List<VcsRepoBinding> findByVcsConnection_Id(Long vcsConnectionId);

    List<VcsRepoBinding> findByWorkspace_IdAndProvider(Long workspaceId, EVcsProvider provider);

    boolean existsByWorkspace_IdAndVcsConnection_IdAndExternalRepoId(
            Long workspaceId, Long connectionId, String externalRepoId);

    // Candidate discovery is not authorization. The caller must authenticate
    // against each binding's project before selecting a unique recipient.
    @Query("SELECT b FROM VcsRepoBinding b " +
           "JOIN FETCH b.project p JOIN FETCH p.workspace " +
           "JOIN FETCH b.workspace JOIN FETCH b.vcsConnection c JOIN FETCH c.workspace " +
           "WHERE b.provider = :provider AND (b.externalRepoId = :externalRepoId " +
           "OR (b.externalRepoId = b.externalRepoSlug AND b.externalRepoSlug = :repoSlug " +
           "AND b.externalNamespace = :namespace))")
    List<VcsRepoBinding> findWebhookCandidates(
            @Param("provider") EVcsProvider provider,
            @Param("externalRepoId") String externalRepoId,
            @Param("repoSlug") String repoSlug,
            @Param("namespace") String namespace);

    @Query("SELECT b FROM VcsRepoBinding b " +
           "LEFT JOIN FETCH b.project " +
           "WHERE b.vcsConnection.id = :connectionId")
    List<VcsRepoBinding> findByVcsConnectionIdWithProject(@Param("connectionId") Long connectionId);

    void deleteByProject_Id(Long projectId);
}

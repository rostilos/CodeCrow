package org.rostilos.codecrow.core.persistence.repository.vcs;

import org.rostilos.codecrow.core.model.vcs.BitbucketConnectInstallation;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

/**
 * Repository for Bitbucket Connect App installations.
 */
@Repository
public interface BitbucketConnectInstallationRepository extends JpaRepository<BitbucketConnectInstallation, Long> {
    
    /**
     * Find installation by client key (unique identifier from Bitbucket).
     */
    Optional<BitbucketConnectInstallation> findByClientKey(String clientKey);
    
    /**
     * Find installation by Bitbucket workspace UUID.
     */
    Optional<BitbucketConnectInstallation> findByBitbucketWorkspaceUuid(String workspaceUuid);
    
    /**
     * Find installation by Bitbucket workspace slug.
     */
    Optional<BitbucketConnectInstallation> findByBitbucketWorkspaceSlug(String workspaceSlug);
    
    /**
     * Find all installations for a CodeCrow workspace.
     */
    List<BitbucketConnectInstallation> findByCodecrowWorkspace_Id(Long codecrowWorkspaceId);
    
    /**
     * Find all enabled installations.
     */
    List<BitbucketConnectInstallation> findByEnabledTrue();
    
    /**
     * Find installation by VCS connection ID.
     */
    Optional<BitbucketConnectInstallation> findByVcsConnection_Id(Long vcsConnectionId);
    
    /**
     * Check if an installation exists for a Bitbucket workspace.
     */
    boolean existsByBitbucketWorkspaceUuid(String workspaceUuid);
    
    /**
     * Delete installation by client key.
     */
    void deleteByClientKey(String clientKey);
}

package org.rostilos.codecrow.webserver.intergration.dto.response;

import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;

import java.time.LocalDateTime;

/**
 * DTO for VCS repository binding information.
 */
public record VcsRepoBindingDTO(
    Long id,
    Long projectId,
    String projectName,
    Long vcsConnectionId,
    EVcsProvider provider,
    String externalRepoId,
    String externalRepoSlug,
    String externalNamespace,
    String displayName,
    String fullName,
    String defaultBranch,
    boolean webhooksConfigured,
    LocalDateTime createdAt
) {
    /**
     * Create DTO from entity.
     */
    public static VcsRepoBindingDTO fromEntity(VcsRepoBinding entity) {
        String projectName = entity.getProject() != null ? entity.getProject().getName() : null;
        String fullName = entity.getExternalNamespace() != null && entity.getExternalRepoSlug() != null
            ? entity.getExternalNamespace() + "/" + entity.getExternalRepoSlug()
            : entity.getDisplayName();
        
        return new VcsRepoBindingDTO(
            entity.getId(),
            entity.getProject() != null ? entity.getProject().getId() : null,
            projectName,
            entity.getVcsConnection() != null ? entity.getVcsConnection().getId() : null,
            entity.getProvider(),
            entity.getExternalRepoId(),
            entity.getExternalRepoSlug(),
            entity.getExternalNamespace(),
            entity.getDisplayName(),
            fullName,
            entity.getDefaultBranch(),
            entity.isWebhooksConfigured(),
            entity.getCreatedAt()
        );
    }
}

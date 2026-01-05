package org.rostilos.codecrow.pipelineagent.webhookhandler;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsRepoBindingRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.Optional;

/**
 * Service for looking up projects from webhook payloads.
 * Provides provider-agnostic project resolution via VcsRepoBinding.
 */
@Service
public class WebhookProjectResolver {
    
    private static final Logger log = LoggerFactory.getLogger(WebhookProjectResolver.class);
    
    private final VcsRepoBindingRepository bindingRepository;
    private final ProjectRepository projectRepository;
    
    public WebhookProjectResolver(
            VcsRepoBindingRepository bindingRepository,
            ProjectRepository projectRepository
    ) {
        this.bindingRepository = bindingRepository;
        this.projectRepository = projectRepository;
    }
    
    /**
     * Find a project by VCS provider and external repository ID.
     * This is the primary lookup method for webhook handling.
     *
     * @param provider The VCS provider
     * @param externalRepoId The external repository ID (UUID for Bitbucket, node_id for GitHub, etc.)
     * @return The project with full connection details, or empty if not found
     */
    public Optional<Project> findProjectByExternalRepo(EVcsProvider provider, String externalRepoId) {
        log.debug("Looking up project for provider={}, externalRepoId={}", provider, externalRepoId);
        
        return bindingRepository.findByProviderAndExternalRepoIdWithDetails(provider, externalRepoId)
                .flatMap(binding -> {
                    Long projectId = binding.getProject().getId();
                    return projectRepository.findByIdWithFullDetails(projectId);
                });
    }
    
    /**
     * Find the VcsRepoBinding by provider and external repository ID.
     *
     * @param provider The VCS provider
     * @param externalRepoId The external repository ID
     * @return The binding, or empty if not found
     */
    public Optional<VcsRepoBinding> findBinding(EVcsProvider provider, String externalRepoId) {
        return bindingRepository.findByProviderAndExternalRepoIdWithDetails(provider, externalRepoId);
    }
    
    /**
     * Validate that a webhook request is authorized for a project.
     * This checks the project auth token against the provided token.
     *
     * @param project The project
     * @param authToken The auth token from the webhook URL
     * @return true if authorized
     */
    public boolean validateWebhookAuth(Project project, String authToken) {
        if (project.getAuthToken() == null || authToken == null) {
            return false;
        }
        return project.getAuthToken().equals(authToken);
    }
    
    /**
     * Normalize a repository ID for lookup.
     * Bitbucket UUIDs come with braces, GitHub IDs are plain, etc.
     *
     * @param provider The VCS provider
     * @param repoId The repository ID as received
     * @return Normalized repository ID
     */
    public String normalizeRepoId(EVcsProvider provider, String repoId) {
        if (repoId == null) return null;
        
        return switch (provider) {
            case BITBUCKET_CLOUD -> repoId.replace("{", "").replace("}", "");
            default -> repoId;
        };
    }
}

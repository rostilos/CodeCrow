package org.rostilos.codecrow.pipelineagent.generic.webhookhandler;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsRepoBindingRepository;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
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
    private final TokenEncryptionService tokenEncryptionService;
    
    public WebhookProjectResolver(
            VcsRepoBindingRepository bindingRepository,
            ProjectRepository projectRepository,
            TokenEncryptionService tokenEncryptionService
    ) {
        this.bindingRepository = bindingRepository;
        this.projectRepository = projectRepository;
        this.tokenEncryptionService = tokenEncryptionService;
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
        return findProjectByExternalRepo(provider, externalRepoId, null);
    }

    /**
     * Find a project by VCS provider and external repository ID, with slug fallback.
     *
     * @param provider The VCS provider
     * @param externalRepoId The external repository UUID/ID from the webhook
     * @param repoSlug The repository slug from the webhook (used for fallback lookup)
     * @return The project with full connection details, or empty if not found
     */
    public Optional<Project> findProjectByExternalRepo(EVcsProvider provider, String externalRepoId, String repoSlug) {
        log.debug("Looking up project for provider={}, externalRepoId={}, repoSlug={}", provider, externalRepoId, repoSlug);
        
        // 1. Primary lookup: externalRepoId column matches the UUID from webhook
        Optional<Project> result = bindingRepository.findByProviderAndExternalRepoIdWithDetails(provider, externalRepoId)
                .flatMap(binding -> projectRepository.findByIdWithFullDetails(binding.getProject().getId()));
        if (result.isPresent()) {
            return result;
        }
        
        // 2. Fallback: older bindings stored the slug as externalRepoId (when repositoryId was null)
        //    Try matching externalRepoId column with the slug from webhook payload
        if (repoSlug != null && !repoSlug.equals(externalRepoId)) {
            log.debug("UUID lookup failed, trying externalRepoId=slug fallback for slug={}", repoSlug);
            result = bindingRepository.findByProviderAndExternalRepoIdWithDetails(provider, repoSlug)
                    .flatMap(binding -> projectRepository.findByIdWithFullDetails(binding.getProject().getId()));
            if (result.isPresent()) {
                return result;
            }
        }
        
        // 3. Final fallback: match against externalRepoSlug column
        String slugToSearch = repoSlug != null ? repoSlug : externalRepoId;
        log.debug("ID lookups failed, trying externalRepoSlug fallback for slug={}", slugToSearch);
        return bindingRepository.findByProviderAndExternalRepoSlugWithDetails(provider, slugToSearch)
                .flatMap(binding -> projectRepository.findByIdWithFullDetails(binding.getProject().getId()));
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
        String storedToken = project.getAuthToken();

        // Try decrypting first (new encrypted tokens)
        try {
            String decryptedToken = tokenEncryptionService.decrypt(storedToken);
            return decryptedToken.equals(authToken);
        } catch (Exception e) {
            // Decryption failed — token is likely stored as plaintext (legacy).
            // Fall back to direct comparison.
            log.debug("Token decryption failed for project {} — trying plaintext comparison", project.getId());
            return storedToken.equals(authToken);
        }
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

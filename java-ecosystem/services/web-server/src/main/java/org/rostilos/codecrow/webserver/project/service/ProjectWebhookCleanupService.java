package org.rostilos.codecrow.webserver.project.service;

import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.EVcsSetupStatus;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.webserver.exception.IntegrationException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;

/**
 * Deletes only the provider webhook recorded on one project/repository binding.
 */
@Service
public class ProjectWebhookCleanupService {

    private static final Logger log = LoggerFactory.getLogger(ProjectWebhookCleanupService.class);

    private final VcsClientProvider vcsClientProvider;
    private final VcsConnectionRepository vcsConnectionRepository;

    public ProjectWebhookCleanupService(
            VcsClientProvider vcsClientProvider,
            VcsConnectionRepository vcsConnectionRepository) {
        this.vcsClientProvider = vcsClientProvider;
        this.vcsConnectionRepository = vcsConnectionRepository;
    }

    /**
     * Delete the exact stored webhook ID from the exact bound repository.
     *
     * <p>This method deliberately does not list hooks, match by host, or delete
     * every CodeCrow-looking URL. Other projects may use the same repository
     * with a different installation and must remain untouched.</p>
     */
    public void deleteProjectWebhook(VcsRepoBinding binding) {
        deleteProjectWebhook(binding, null);
    }

    /**
     * Delete a binding's exact webhook, optionally using the replacement
     * connection if the old connection no longer has access to the repository.
     *
     * <p>A GitHub installation can be replaced while the local connection row
     * remains. In that case the refreshed installation may target a different
     * account and GitHub returns 403 for the old repository. Cleanup may retry
     * with another connected credential from the same CodeCrow workspace, but
     * it still deletes only the stored webhook ID from the stored repository.
     * It never lists or matches other hooks.</p>
     */
    public void deleteProjectWebhook(
            VcsRepoBinding binding,
            VcsConnection replacementConnection) {
        if (binding == null || binding.getWebhookId() == null || binding.getWebhookId().isBlank()) {
            return;
        }

        VcsConnection connection = binding.getVcsConnection();
        if (connection == null) {
            throw new IntegrationException(
                    "Cannot remove project webhook because its VCS connection is missing");
        }

        String namespace = binding.getExternalNamespace();
        String repoSlug = binding.getExternalRepoSlug();
        if (connection.getConnectionType() == EVcsConnectionType.REPOSITORY_TOKEN
                && connection.getRepositoryPath() != null
                && !connection.getRepositoryPath().isBlank()) {
            String repositoryPath = connection.getRepositoryPath();
            int lastSlash = repositoryPath.lastIndexOf('/');
            if (lastSlash > 0) {
                namespace = repositoryPath.substring(0, lastSlash);
                repoSlug = repositoryPath.substring(lastSlash + 1);
            } else {
                repoSlug = repositoryPath;
            }
        }

        if (namespace == null || namespace.isBlank() || repoSlug == null || repoSlug.isBlank()) {
            throw new IntegrationException(
                    "Cannot remove project webhook because its repository coordinates are incomplete");
        }

        Exception failure = tryDelete(binding, connection, namespace, repoSlug);
        if (failure == null) {
            return;
        }

        Map<String, VcsConnection> alternatives = new LinkedHashMap<>();
        addAlternative(alternatives, connection, replacementConnection);

        Long workspaceId = workspaceId(connection);
        if (connection.getProviderType() == EVcsProvider.GITHUB && workspaceId != null) {
            for (VcsConnection candidate : vcsConnectionRepository
                    .findByWorkspace_IdAndProviderType(workspaceId, EVcsProvider.GITHUB)) {
                if (repositoryOwnerMatches(candidate, namespace)) {
                    addAlternative(alternatives, connection, candidate);
                }
            }
        }

        for (VcsConnection alternative : alternatives.values()) {
            Exception alternativeFailure =
                    tryDelete(binding, alternative, namespace, repoSlug);
            if (alternativeFailure == null) {
                log.info(
                        "Deleted project-owned webhook {} using fallback connection {} "
                                + "after connection {} lost repository access",
                        binding.getWebhookId(),
                        alternative.getId(),
                        connection.getId());
                return;
            }
            failure = alternativeFailure;
        }

        throw new IntegrationException(
                "Could not remove the project-owned webhook. "
                        + "The project/VCS binding was kept so cleanup can be retried: "
                        + failure.getMessage(),
                failure);
    }

    private Exception tryDelete(
            VcsRepoBinding binding,
            VcsConnection connection,
            String namespace,
            String repoSlug) {
        try {
            VcsClient client = vcsClientProvider.getClient(connection);
            client.deleteWebhook(namespace, repoSlug, binding.getWebhookId());
            log.info("Deleted project-owned webhook {} from {}/{} for binding {} using connection {}",
                    binding.getWebhookId(), namespace, repoSlug, binding.getId(), connection.getId());
            return null;
        } catch (Exception e) {
            log.warn("Connection {} could not delete project-owned webhook {} from {}/{}: {}",
                    connection.getId(), binding.getWebhookId(), namespace, repoSlug, e.getMessage());
            return e;
        }
    }

    private void addAlternative(
            Map<String, VcsConnection> alternatives,
            VcsConnection original,
            VcsConnection candidate) {
        if (candidate == null
                || candidate == original
                || candidate.getSetupStatus() != EVcsSetupStatus.CONNECTED
                || candidate.getProviderType() != original.getProviderType()
                || !Objects.equals(workspaceId(original), workspaceId(candidate))) {
            return;
        }
        String key = candidate.getId() == null
                ? "object:" + System.identityHashCode(candidate)
                : "id:" + candidate.getId();
        alternatives.putIfAbsent(key, candidate);
    }

    private boolean repositoryOwnerMatches(VcsConnection connection, String namespace) {
        if (namespace == null || namespace.isBlank()) {
            return false;
        }
        if (connection.getExternalWorkspaceSlug() != null
                && connection.getExternalWorkspaceSlug().equalsIgnoreCase(namespace)) {
            return true;
        }
        String repositoryPath = connection.getRepositoryPath();
        if (connection.getConnectionType() == EVcsConnectionType.REPOSITORY_TOKEN
                && repositoryPath != null) {
            int lastSlash = repositoryPath.lastIndexOf('/');
            return lastSlash > 0
                    && repositoryPath.substring(0, lastSlash).equalsIgnoreCase(namespace);
        }
        return false;
    }

    private Long workspaceId(VcsConnection connection) {
        return connection == null || connection.getWorkspace() == null
                ? null
                : connection.getWorkspace().getId();
    }
}

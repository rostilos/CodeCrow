package org.rostilos.codecrow.pipelineagent.generic.webhookhandler;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsRepoBindingRepository;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.springframework.stereotype.Service;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Optional;
import java.util.function.Predicate;

/** Resolves a repository only inside an authenticated project scope. */
@Service
public class WebhookProjectResolver {
    private final VcsRepoBindingRepository bindings;
    private final ProjectRepository projects;
    private final TokenEncryptionService encryption;

    public WebhookProjectResolver(VcsRepoBindingRepository bindings,
                                  ProjectRepository projects,
                                  TokenEncryptionService encryption) {
        this.bindings = bindings;
        this.projects = projects;
        this.encryption = encryption;
    }

    public Optional<Project> findAuthenticatedProject(EVcsProvider provider, String repoId,
            String slug, String namespace, Predicate<Project> authenticate) {
        Project match = null;
        for (VcsRepoBinding binding : bindings.findWebhookCandidates(
                provider, normalizeRepoId(provider, repoId), slug, namespace)) {
            // A denormalized binding must never bridge workspace boundaries.
            if (binding.getWorkspace() == null || binding.getProject() == null
                    || binding.getVcsConnection() == null
                    || binding.getProject().getWorkspace() == null
                    || binding.getVcsConnection().getWorkspace() == null
                    || !binding.getWorkspace().getId().equals(binding.getProject().getWorkspace().getId())
                    || !binding.getWorkspace().getId().equals(binding.getVcsConnection().getWorkspace().getId())) {
                continue;
            }
            Project candidate = projects.findByIdWithFullDetails(binding.getProject().getId()).orElse(null);
            if (candidate == null || !authenticate.test(candidate)) {
                continue;
            }
            if (match != null && !match.getId().equals(candidate.getId())) {
                throw new IllegalArgumentException(
                        "Webhook matches multiple authenticated projects; use the project's token URL");
            }
            match = candidate;
        }
        return Optional.ofNullable(match);
    }

    public boolean validateWebhookAuth(Project project, String token) {
        String expected = webhookSecret(project);
        return expected != null && token != null && !expected.isBlank()
                && MessageDigest.isEqual(expected.getBytes(StandardCharsets.UTF_8),
                                         token.getBytes(StandardCharsets.UTF_8));
    }

    /** Existing project auth token is also the manually configured native webhook secret. */
    public String webhookSecret(Project project) {
        String stored = project.getAuthToken();
        if (stored == null || stored.isBlank()) return null;
        try {
            return encryption.decrypt(stored);
        } catch (Exception legacyPlaintext) {
            return stored;
        }
    }

    public String normalizeRepoId(EVcsProvider provider, String repoId) {
        if (repoId == null) return null;
        return provider == EVcsProvider.BITBUCKET_CLOUD
                ? repoId.replace("{", "").replace("}", "") : repoId;
    }
}

package org.rostilos.codecrow.pipelineagent.generic.service;

import com.fasterxml.jackson.databind.JsonNode;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.service.SiteSettingsProvider;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookProjectResolver;
import org.springframework.stereotype.Service;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;

/** Provider-native authentication over the original request bytes. */
@Service
public class NativeWebhookAuthenticator {
    private final WebhookProjectResolver projects;
    private final SiteSettingsProvider settings;

    public NativeWebhookAuthenticator(WebhookProjectResolver projects, SiteSettingsProvider settings) {
        this.projects = projects;
        this.settings = settings;
    }

    public boolean authenticate(Project project, EVcsProvider provider, JsonNode payload,
                                byte[] body, String githubSignature, String bitbucketSignature,
                                String gitlabToken) {
        return switch (provider) {
            case GITLAB -> projects.validateWebhookAuth(project, gitlabToken);
            case BITBUCKET_CLOUD -> validHmac(body, bitbucketSignature, projects.webhookSecret(project));
            case GITHUB -> validHmac(body, githubSignature, projects.webhookSecret(project))
                    || validGitHubApp(project, payload, body, githubSignature);
            case BITBUCKET_SERVER -> false;
        };
    }

    private boolean validGitHubApp(Project project, JsonNode payload, byte[] body, String signature) {
        VcsRepoInfo repo = project.getEffectiveVcsRepoInfo();
        if (repo == null || repo.getVcsConnection() == null
                || (repo.getVcsConnection().getConnectionType() != EVcsConnectionType.GITHUB_APP
                    && repo.getVcsConnection().getConnectionType() != EVcsConnectionType.APP)) {
            return false;
        }
        String installation = repo.getVcsConnection().getInstallationId();
        if (installation == null || !installation.equals(payload.path("installation").path("id").asText())) {
            return false;
        }
        return validHmac(body, signature, settings.getGitHubSettings().webhookSecret());
    }

    static boolean validHmac(byte[] body, String signature, String secret) {
        if (secret == null || secret.isBlank() || signature == null
                || !signature.matches("sha256=[0-9a-fA-F]{64}")) return false;
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            return MessageDigest.isEqual(mac.doFinal(body), HexFormat.of().parseHex(signature.substring(7)));
        } catch (java.security.GeneralSecurityException | IllegalArgumentException invalid) {
            return false;
        }
    }
}

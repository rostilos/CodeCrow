package org.rostilos.codecrow.vcsclient.gitlab;

import org.rostilos.codecrow.core.dto.admin.GitLabSettingsDTO;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;

/**
 * One inseparable GitLab OAuth issuer and credential pair.
 *
 * <p>OAuth client credentials are registered on one GitLab instance. Keeping
 * the issuer and credentials in one value prevents a tenant-controlled
 * connection URL from being combined with the deployment-wide secret.</p>
 */
public final class GitLabOAuthProvider {

    private final String instanceBaseUrl;
    private final String clientId;
    private final String clientSecret;

    private GitLabOAuthProvider(
            String instanceBaseUrl,
            String clientId,
            String clientSecret
    ) {
        this.instanceBaseUrl = canonicalIssuer(instanceBaseUrl);
        if (clientId == null || clientId.isBlank()
                || clientSecret == null || clientSecret.isBlank()) {
            throw new GitLabOAuthConfigurationException(
                    "GitLab OAuth application credentials are not configured");
        }
        this.clientId = clientId;
        this.clientSecret = clientSecret;
    }

    public static GitLabOAuthProvider from(GitLabSettingsDTO settings) {
        if (settings == null) {
            throw new GitLabOAuthConfigurationException(
                    "GitLab OAuth application settings are not configured");
        }
        return new GitLabOAuthProvider(
                settings.baseUrl(),
                settings.clientId(),
                settings.clientSecret());
    }

    /**
     * Require a persisted connection to represent this provider's issuer.
     */
    public GitLabOAuthProvider requireConnectionIssuer(VcsConnection connection) {
        return requireIssuer(GitLabConfig.instanceBaseUrl(connection));
    }

    /**
     * Require an instance URL to represent this provider's issuer.
     */
    public GitLabOAuthProvider requireIssuer(String candidateBaseUrl) {
        String candidate = canonicalIssuer(candidateBaseUrl);
        if (!instanceBaseUrl.equals(candidate)) {
            throw new GitLabOAuthConfigurationException(
                    "GitLab OAuth connection issuer " + candidate
                            + " does not match the configured OAuth issuer "
                            + instanceBaseUrl
                            + ". Update the token connection or configure the "
                            + "deployment OAuth application for this GitLab instance.");
        }
        return this;
    }

    public String instanceBaseUrl() {
        return instanceBaseUrl;
    }

    public String clientId() {
        return clientId;
    }

    public String clientSecret() {
        return clientSecret;
    }

    public static boolean sameIssuer(String first, String second) {
        return canonicalIssuer(first).equals(canonicalIssuer(second));
    }

    /**
     * Canonicalize the GitLab instance root for exact issuer comparisons.
     */
    public static String canonicalIssuer(String configuredBaseUrl) {
        String normalized = GitLabConfig.instanceBaseUrl(configuredBaseUrl);
        final URI parsed;
        try {
            parsed = new URI(normalized).normalize();
        } catch (URISyntaxException e) {
            throw new GitLabOAuthConfigurationException(
                    "Invalid GitLab OAuth issuer URL", e);
        }

        if (!parsed.isAbsolute() || parsed.getScheme() == null
                || parsed.getHost() == null || parsed.getHost().isBlank()) {
            throw new GitLabOAuthConfigurationException(
                    "GitLab OAuth issuer must be an absolute URL with a hostname");
        }
        if (parsed.getUserInfo() != null || parsed.getQuery() != null
                || parsed.getFragment() != null) {
            throw new GitLabOAuthConfigurationException(
                    "GitLab OAuth issuer must not contain credentials, a query, or a fragment");
        }

        String scheme = parsed.getScheme().toLowerCase(Locale.ROOT);
        if (!"https".equals(scheme) && !"http".equals(scheme)) {
            throw new GitLabOAuthConfigurationException(
                    "GitLab OAuth issuer must use HTTP or HTTPS");
        }
        String host = parsed.getHost().toLowerCase(Locale.ROOT);
        int port = parsed.getPort();
        if (("https".equals(scheme) && port == 443)
                || ("http".equals(scheme) && port == 80)) {
            port = -1;
        }

        String path = parsed.getPath();
        if (path == null || "/".equals(path)) {
            path = "";
        } else {
            path = path.replaceAll("/+$", "");
        }

        try {
            return new URI(scheme, null, host, port, path, null, null).toASCIIString();
        } catch (URISyntaxException e) {
            throw new GitLabOAuthConfigurationException(
                    "Invalid GitLab OAuth issuer URL", e);
        }
    }
}

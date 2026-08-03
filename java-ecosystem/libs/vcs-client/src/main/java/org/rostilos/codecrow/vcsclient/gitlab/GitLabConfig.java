package org.rostilos.codecrow.vcsclient.gitlab;

import org.rostilos.codecrow.core.model.vcs.VcsConnection;

/**
 * Configuration constants for GitLab API access.
 */
public final class GitLabConfig {

    public static final String INSTANCE_BASE =
            org.rostilos.codecrow.core.model.vcs.config.gitlab.GitLabConfig.DEFAULT_BASE_URL;
    public static final String API_BASE = INSTANCE_BASE + "/api/v4";
    public static final int DEFAULT_PAGE_SIZE = 20;

    private GitLabConfig() {
        // Utility class
    }

    /**
     * Normalize a configured GitLab instance URL.
     *
     * <p>The persisted setting is the instance root (for example,
     * {@code https://gitlab.example.com}), not the REST API URL. Accepting an
     * accidentally supplied {@code /api/v4} suffix keeps older/manual
     * configuration usable without producing {@code /api/v4/api/v4}.</p>
     */
    public static String instanceBaseUrl(String configuredBaseUrl) {
        return org.rostilos.codecrow.core.model.vcs.config.gitlab.GitLabConfig
                .normalizeBaseUrl(configuredBaseUrl);
    }

    /**
     * Resolve the instance represented by a persisted connection.
     *
     * <p>Connections created before instance URLs were supported have no
     * GitLab configuration. They always represent GitLab.com, independently
     * of the deployment-wide OAuth setting.</p>
     */
    public static String instanceBaseUrl(VcsConnection connection) {
        if (connection != null && connection.getConfiguration()
                instanceof org.rostilos.codecrow.core.model.vcs.config.gitlab.GitLabConfig config) {
            return instanceBaseUrl(config.effectiveBaseUrl());
        }
        return INSTANCE_BASE;
    }

    /**
     * Return the REST v4 base URL for a GitLab instance root.
     */
    public static String apiBaseUrl(String configuredBaseUrl) {
        return instanceBaseUrl(configuredBaseUrl) + "/api/v4";
    }
}

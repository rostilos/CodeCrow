package org.rostilos.codecrow.taskmanagement.jira.cloud;

/**
 * Configuration for connecting to a Jira Cloud instance.
 *
 * @param baseUrl  Jira Cloud base URL, e.g. "https://myorg.atlassian.net"
 * @param email    Atlassian account email used for API authentication
 * @param apiToken API token generated from https://id.atlassian.com/manage-profile/security/api-tokens
 */
public record JiraCloudConfig(
        String baseUrl,
        String email,
        String apiToken
) {
    public JiraCloudConfig {
        if (baseUrl == null || baseUrl.isBlank()) {
            throw new IllegalArgumentException("Jira Cloud base URL must not be blank");
        }
        if (email == null || email.isBlank()) {
            throw new IllegalArgumentException("Jira Cloud email must not be blank");
        }
        if (apiToken == null || apiToken.isBlank()) {
            throw new IllegalArgumentException("Jira Cloud API token must not be blank");
        }
        // Normalise: strip trailing slash
        baseUrl = baseUrl.replaceAll("/+$", "");
    }

    /**
     * Build the Basic Auth header value ({@code email:apiToken} base64-encoded).
     */
    public String basicAuthHeader() {
        String credentials = email + ":" + apiToken;
        return "Basic " + java.util.Base64.getEncoder().encodeToString(credentials.getBytes());
    }
}

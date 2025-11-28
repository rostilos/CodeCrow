package org.rostilos.codecrow.webserver.dto.request.project;

public class UpdateRepositorySettingsRequest {
    // single encrypted token field as agreed
    private String token; // new token value (optional in PATCH)
    private String apiBaseUrl; // optional
    private String webhookSecret; // optional

    public String getToken() {
        return token;
    }

    public void setToken(String token) {
        this.token = token;
    }

    public String getApiBaseUrl() {
        return apiBaseUrl;
    }

    public void setApiBaseUrl(String apiBaseUrl) {
        this.apiBaseUrl = apiBaseUrl;
    }

    public String getWebhookSecret() {
        return webhookSecret;
    }

    public void setWebhookSecret(String webhookSecret) {
        this.webhookSecret = webhookSecret;
    }
}

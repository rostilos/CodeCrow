package org.rostilos.codecrow.webserver.dto.request.vcs.github;

import com.fasterxml.jackson.annotation.JsonProperty;

public class GitHubCreateRequest {
    
    @JsonProperty("accessToken")
    private String accessToken;

    private String organizationId;
    
    private String connectionName;

    public String getAccessToken() {
        return accessToken;
    }

    public void setAccessToken(String accessToken) {
        this.accessToken = accessToken;
    }

    public String getOrganizationId() {
        return organizationId;
    }

    public void setOrganizationId(String organizationId) {
        this.organizationId = organizationId;
    }

    public String getConnectionName() {
        return connectionName;
    }

    public void setConnectionName(String connectionName) {
        this.connectionName = connectionName;
    }
}

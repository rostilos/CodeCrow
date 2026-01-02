package org.rostilos.codecrow.webserver.vcs.dto.request.cloud;

import com.fasterxml.jackson.annotation.JsonProperty;

public class BitbucketCloudCreateRequest {
    private String workspaceId;

    @JsonProperty("oAuthKey")
    private String oAuthKey;

    @JsonProperty("oAuthSecret")
    private String oAuthSecret;

    private String connectionName;

    public String getWorkspaceId() {
        return workspaceId;
    }

    public void setWorkspaceId(String workspaceId) {
        this.workspaceId = workspaceId;
    }

    public String getOAuthKey() {
        return oAuthKey;
    }

    public void setOAuthKey(String oAuthKey) {
        this.oAuthKey = oAuthKey;
    }

    public String getOAuthSecret() {
        return oAuthSecret;
    }

    public void setOAuthSecret(String oAuthSecret) {
        this.oAuthSecret = oAuthSecret;
    }

    public String getConnectionName() {
        return connectionName;
    }
}

package org.rostilos.codecrow.webserver.vcs.dto.request.gitlab;

import com.fasterxml.jackson.annotation.JsonProperty;

public class GitLabCreateRequest {
    
    @JsonProperty("accessToken")
    private String accessToken;

    private String groupId;
    
    private String connectionName;

    public String getAccessToken() {
        return accessToken;
    }

    public void setAccessToken(String accessToken) {
        this.accessToken = accessToken;
    }

    public String getGroupId() {
        return groupId;
    }

    public void setGroupId(String groupId) {
        this.groupId = groupId;
    }

    public String getConnectionName() {
        return connectionName;
    }

    public void setConnectionName(String connectionName) {
        this.connectionName = connectionName;
    }
}

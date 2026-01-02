package org.rostilos.codecrow.webserver.vcs.dto.response;

public class InternalVcsConnectionDto {
    public String clientId;
    public String clientSecret;
    public String providerId;
    public String workspace;
    public String repoSlug;

    public InternalVcsConnectionDto() {}

    public InternalVcsConnectionDto(String clientId, String clientSecret, String providerId, String workspace, String repoSlug) {
        this.clientId = clientId;
        this.clientSecret = clientSecret;
        this.providerId = providerId;
        this.workspace = workspace;
        this.repoSlug = repoSlug;
    }
}

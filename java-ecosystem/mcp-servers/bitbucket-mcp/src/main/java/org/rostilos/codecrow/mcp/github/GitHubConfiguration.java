package org.rostilos.codecrow.mcp.github;

public class GitHubConfiguration {
    
    private final String accessToken;
    private final String owner;
    private final String repo;
    private final String prNumber;

    public GitHubConfiguration(String accessToken, String owner, String repo, String prNumber) {
        this.accessToken = accessToken;
        this.owner = owner;
        this.repo = repo;
        this.prNumber = prNumber;
    }

    public String getAccessToken() {
        return accessToken;
    }

    public String getOwner() {
        return owner;
    }

    public String getRepo() {
        return repo;
    }

    public String getPrNumber() {
        return prNumber;
    }
}

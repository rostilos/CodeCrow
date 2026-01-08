package org.rostilos.codecrow.mcp.bitbucket;

public class BitbucketConfiguration {

    private final String repository;
    private final String workspace;

    public BitbucketConfiguration(String workspace, String repository) {
        this.repository = repository;
        this.workspace = workspace;
    }

    public String getRepository() {
        return repository;
    }

    public String getWorkspace() {
        return workspace;
    }
}

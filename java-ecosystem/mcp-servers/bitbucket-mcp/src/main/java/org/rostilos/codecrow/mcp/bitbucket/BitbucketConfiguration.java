package org.rostilos.codecrow.mcp.bitbucket;

public class BitbucketConfiguration {
    private static final String UUID_PATTERN = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
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
        return formatWorkspaceId(workspace);
    }

    private static String formatWorkspaceId(String workspaceId) {
        if (workspaceId == null) {
            return null;
        }
        // If it's already wrapped in braces, return as-is
        if (workspaceId.startsWith("{") && workspaceId.endsWith("}")) {
            return workspaceId;
        }
        // If it matches UUID pattern, wrap in braces
        if (workspaceId.matches(UUID_PATTERN)) {
            return "{" + workspaceId + "}";
        }
        // Otherwise return as-is (slug)
        return workspaceId;
    }
}

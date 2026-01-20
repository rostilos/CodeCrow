package org.rostilos.codecrow.vcsclient;

import org.rostilos.codecrow.vcsclient.model.*;

import java.io.IOException;
import java.util.List;

/**
 * Generic VCS client interface.
 * Provider-specific implementations (BitbucketCloudClient, GitHubClient, etc.)
 * implement this interface to provide consistent access to VCS functionality.
 */
public interface VcsClient {

    /**
     * Validate that the connection is working (e.g., token is valid).
     * @return true if the connection is valid
     */
    boolean validateConnection() throws IOException;

    /**
     * List workspaces/organizations the authenticated user has access to.
     * @return list of VCS workspaces
     */
    List<VcsWorkspace> listWorkspaces() throws IOException;

    /**
     * List repositories in a workspace/organization.
     * @param workspaceId the external workspace/org ID
     * @param page page number (1-based)
     * @return paginated repository list
     */
    VcsRepositoryPage listRepositories(String workspaceId, int page) throws IOException;

    /**
     * Search repositories in a workspace/organization.
     * @param workspaceId the external workspace/org ID
     * @param query search query
     * @param page page number (1-based)
     * @return paginated repository list
     */
    VcsRepositoryPage searchRepositories(String workspaceId, String query, int page) throws IOException;

    /**
     * Get details of a specific repository.
     * @param workspaceId the external workspace/org ID
     * @param repoIdOrSlug the repository ID or slug
     * @return repository details
     */
    VcsRepository getRepository(String workspaceId, String repoIdOrSlug) throws IOException;

    /**
     * Create or update a webhook for a repository.
     * @param workspaceId the external workspace/org ID
     * @param repoIdOrSlug the repository ID or slug
     * @param targetUrl the webhook target URL
     * @param events list of events to subscribe to
     * @return the webhook ID
     */
    String ensureWebhook(String workspaceId, String repoIdOrSlug, String targetUrl, List<String> events) throws IOException;

    /**
     * Delete a webhook from a repository.
     * @param workspaceId the external workspace/org ID
     * @param repoIdOrSlug the repository ID or slug
     * @param webhookId the webhook ID to delete
     */
    void deleteWebhook(String workspaceId, String repoIdOrSlug, String webhookId) throws IOException;

    /**
     * List webhooks for a repository.
     * @param workspaceId the external workspace/org ID
     * @param repoIdOrSlug the repository ID or slug
     * @return list of webhooks
     */
    List<VcsWebhook> listWebhooks(String workspaceId, String repoIdOrSlug) throws IOException;

    /**
     * Get the authenticated user's information.
     * @return user info
     */
    VcsUser getCurrentUser() throws IOException;

    /**
     * Get a workspace/organization by ID.
     * @param workspaceId the external workspace/org ID
     * @return workspace info
     */
    VcsWorkspace getWorkspace(String workspaceId) throws IOException;

    /**
     * Get the default branch name for a repository.
     * @param workspaceId the external workspace/org ID
     * @param repoIdOrSlug the repository ID or slug
     * @return default branch name
     */
    default String getDefaultBranch(String workspaceId, String repoIdOrSlug) throws IOException {
        VcsRepository repo = getRepository(workspaceId, repoIdOrSlug);
        return repo != null ? repo.defaultBranch() : null;
    }

    /**
     * Set a repository variable (for CI/CD integration).
     * Optional operation - may not be supported by all providers.
     * @param workspaceId the external workspace/org ID
     * @param repoIdOrSlug the repository ID or slug
     * @param key variable key
     * @param value variable value
     * @param isSecret whether the variable is a secret
     */
    default void setRepoVariable(String workspaceId, String repoIdOrSlug, String key, String value, boolean isSecret) throws IOException {
        throw new UnsupportedOperationException("Repository variables not supported by this provider");
    }

    /**
     * Get the count of repositories in a workspace.
     * @param workspaceId the external workspace/org ID
     * @return number of repositories
     */
    default int getRepositoryCount(String workspaceId) throws IOException {
        VcsRepositoryPage page = listRepositories(workspaceId, 1);
        return page.totalCount() != null ? page.totalCount() : page.items().size();
    }

    /**
     * Download repository archive as a ZIP file.
     * @param workspaceId the external workspace/org ID
     * @param repoIdOrSlug the repository ID or slug
     * @param branchOrCommit the branch name or commit hash to download
     * @return byte array containing the ZIP archive
     * @deprecated Use {@link #downloadRepositoryArchiveToFile} for large repositories to avoid OOM
     */
    @Deprecated
    byte[] downloadRepositoryArchive(String workspaceId, String repoIdOrSlug, String branchOrCommit) throws IOException;

    /**
     * Download repository archive to a file (streaming, memory-efficient).
     * @param workspaceId the external workspace/org ID
     * @param repoIdOrSlug the repository ID or slug
     * @param branchOrCommit the branch name or commit hash to download
     * @param targetFile the file to write the archive to
     * @return the number of bytes downloaded
     */
    long downloadRepositoryArchiveToFile(String workspaceId, String repoIdOrSlug, String branchOrCommit, java.nio.file.Path targetFile) throws IOException;

    /**
     * Get raw file content from repository.
     * @param workspaceId the external workspace/org ID
     * @param repoIdOrSlug the repository ID or slug
     * @param filePath path to the file in the repository
     * @param branchOrCommit branch name or commit hash
     * @return file content as string
     */
    String getFileContent(String workspaceId, String repoIdOrSlug, String filePath, String branchOrCommit) throws IOException;

    /**
     * Get the latest commit hash for a branch.
     * @param workspaceId the external workspace/org ID
     * @param repoIdOrSlug the repository ID or slug
     * @param branchName branch name
     * @return commit hash
     */
    String getLatestCommitHash(String workspaceId, String repoIdOrSlug, String branchName) throws IOException;

    /**
     * List branches in a repository.
     * @param workspaceId the external workspace/org ID
     * @param repoIdOrSlug the repository ID or slug
     * @return list of branch names
     */
    List<String> listBranches(String workspaceId, String repoIdOrSlug) throws IOException;
    
    /**
     * List branches in a repository with search/filter support.
     * @param workspaceId the external workspace/org ID
     * @param repoIdOrSlug the repository ID or slug
     * @param search optional search query to filter branch names (null for all)
     * @param limit maximum number of results to return (0 for unlimited)
     * @return list of branch names matching the search criteria
     */
    default List<String> listBranches(String workspaceId, String repoIdOrSlug, String search, int limit) throws IOException {
        List<String> allBranches = listBranches(workspaceId, repoIdOrSlug);
        
        if (search != null && !search.isEmpty()) {
            String searchLower = search.toLowerCase();
            allBranches = allBranches.stream()
                .filter(b -> b.toLowerCase().contains(searchLower))
                .toList();
        }
        
        if (limit > 0 && allBranches.size() > limit) {
            return allBranches.subList(0, limit);
        }
        
        return allBranches;
    }
    
    /**
     * Get collaborators/members with access to a repository.
     * Returns users with their permission levels.
     * @param workspaceId the external workspace/org ID
     * @param repoIdOrSlug the repository ID or slug
     * @return list of collaborators with permissions
     */
    default List<VcsCollaborator> getRepositoryCollaborators(String workspaceId, String repoIdOrSlug) throws IOException {
        throw new UnsupportedOperationException("Repository collaborators not supported by this provider");
    }
    
    /**
     * Get the diff between two branches.
     * Used for multi-branch indexing that captures differences between branches.
     * @param workspaceId the external workspace/org ID
     * @param repoIdOrSlug the repository ID or slug
     * @param baseBranch the base branch to compare from (e.g., "main")
     * @param compareBranch the branch to compare (e.g., "release/1.0")
     * @return raw diff string in unified diff format
     */
    default String getBranchDiff(String workspaceId, String repoIdOrSlug, String baseBranch, String compareBranch) throws IOException {
        throw new UnsupportedOperationException("Branch diff not supported by this provider");
    }
}

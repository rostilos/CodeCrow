package org.rostilos.codecrow.vcsclient.model;

/**
 * Represents a repository in a VCS provider.
 * Common fields across Bitbucket, GitHub, GitLab, etc.
 */
public record VcsRepository(
    /**
     * The stable unique identifier for the repository.
     * - Bitbucket Cloud: UUID (without braces)
     * - GitHub: node_id or numeric id
     * - GitLab: project id
     */
    String id,
    
    /**
     * Human-readable repository name/slug.
     */
    String slug,
    
    /**
     * Display name (may differ from slug).
     */
    String name,
    
    /**
     * Full name including namespace (e.g., "org/repo").
     */
    String fullName,
    
    /**
     * Repository description.
     */
    String description,
    
    /**
     * Whether the repository is private.
     */
    boolean isPrivate,
    
    /**
     * Default branch name.
     */
    String defaultBranch,
    
    /**
     * Clone URL (HTTPS).
     */
    String cloneUrl,
    
    /**
     * Web URL for the repository.
     */
    String htmlUrl,
    
    /**
     * Workspace/namespace the repo belongs to.
     */
    String namespace,
    
    /**
     * Avatar URL for the repository or owner.
     */
    String avatarUrl
) {
    /**
     * Create a minimal VcsRepository with just ID and slug.
     */
    public static VcsRepository minimal(String id, String slug, String namespace) {
        return new VcsRepository(id, slug, slug, namespace + "/" + slug, null, true, null, null, null, namespace, null);
    }
}

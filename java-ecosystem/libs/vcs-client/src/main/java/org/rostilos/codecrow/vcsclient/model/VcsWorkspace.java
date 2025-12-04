package org.rostilos.codecrow.vcsclient.model;

/**
 * Represents a workspace/organization in a VCS provider.
 * - Bitbucket Cloud: Workspace
 * - GitHub: Organization or User
 * - GitLab: Group or User namespace
 */
public record VcsWorkspace(
    /**
     * Unique identifier (UUID for Bitbucket, numeric ID for GitHub/GitLab).
     */
    String id,
    
    /**
     * URL-friendly slug/login.
     */
    String slug,
    
    /**
     * Display name.
     */
    String name,
    
    /**
     * Whether this is a user namespace (vs organization/group).
     */
    boolean isUser,
    
    /**
     * Avatar URL.
     */
    String avatarUrl,
    
    /**
     * Web URL.
     */
    String htmlUrl
) {
    /**
     * Create a minimal VcsWorkspace with just ID and slug.
     */
    public static VcsWorkspace minimal(String id, String slug) {
        return new VcsWorkspace(id, slug, slug, false, null, null);
    }
}

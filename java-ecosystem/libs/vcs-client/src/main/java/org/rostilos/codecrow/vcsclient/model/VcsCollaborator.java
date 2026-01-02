package org.rostilos.codecrow.vcsclient.model;

/**
 * Represents a repository collaborator/member with their permission level.
 * 
 * @param userId Unique user ID in the VCS system
 * @param username The user's login/username
 * @param displayName Human-readable display name
 * @param avatarUrl URL to the user's avatar image
 * @param permission Permission level (e.g., "read", "write", "admin")
 * @param htmlUrl URL to the user's profile page
 */
public record VcsCollaborator(
    String userId,
    String username,
    String displayName,
    String avatarUrl,
    String permission,
    String htmlUrl
) {
    /**
     * Check if this collaborator has write access or higher.
     */
    public boolean hasWriteAccess() {
        if (permission == null) return false;
        String p = permission.toLowerCase();
        return p.equals("write") || p.equals("admin") || p.equals("owner") 
            || p.equals("maintain") || p.equals("push");
    }
    
    /**
     * Check if this collaborator has admin access.
     */
    public boolean hasAdminAccess() {
        if (permission == null) return false;
        String p = permission.toLowerCase();
        return p.equals("admin") || p.equals("owner");
    }
}

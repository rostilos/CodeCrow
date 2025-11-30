package org.rostilos.codecrow.vcsclient.model;

/**
 * Represents an authenticated user in a VCS provider.
 */
public record VcsUser(
    /**
     * Unique identifier.
     */
    String id,
    
    /**
     * Username/login.
     */
    String username,
    
    /**
     * Display name.
     */
    String displayName,
    
    /**
     * Email address.
     */
    String email,
    
    /**
     * Avatar URL.
     */
    String avatarUrl,
    
    /**
     * Profile URL.
     */
    String htmlUrl
) {
    /**
     * Create a minimal VcsUser with just ID and username.
     */
    public static VcsUser minimal(String id, String username) {
        return new VcsUser(id, username, username, null, null, null);
    }
}

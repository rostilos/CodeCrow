package org.rostilos.codecrow.core.model.vcs;

import java.util.Locale;

/**
 * Enumeration of supported VCS providers.
 * Used to identify the type of version control system.
 */
public enum EVcsProvider {
    BITBUCKET_CLOUD("bitbucket-cloud"),
    BITBUCKET_SERVER("bitbucket-server"),
    GITHUB("github"),
    GITLAB("gitlab");

    private final String id;

    EVcsProvider(String id) {
        this.id = id;
    }

    public String getId() {
        return id;
    }

    public static EVcsProvider fromId(String providerId) {
        if (providerId == null) {
            throw new IllegalArgumentException("Provider ID cannot be null");
        }
        
        String normalized = providerId.toLowerCase(Locale.ENGLISH).replace('_', '-');
        for (EVcsProvider provider : values()) {
            if (provider.id.equals(normalized)) {
                return provider;
            }
        }
        
        // Fallback to enum name matching for backwards compatibility
        try {
            return valueOf(providerId.toUpperCase(Locale.ENGLISH).replace('-', '_'));
        } catch (IllegalArgumentException e) {
            throw new IllegalArgumentException("Unknown VCS provider: " + providerId);
        }
    }
}

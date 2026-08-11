package org.rostilos.codecrow.webserver.publicshare;

import org.springframework.security.core.Authentication;

import java.util.Optional;

/** Resolves one internal resource type into its explicitly sanitized public DTO. */
public interface PublicShareResourceProvider {

    String resourceType();

    Optional<?> getPublicPreview(String resourceKey);

    /**
     * Returns a protected in-app destination only when the current principal is
     * authorized for the underlying tenant resource.
     */
    default Optional<String> getAuthorizedPath(String resourceKey, Authentication authentication) {
        return Optional.empty();
    }
}

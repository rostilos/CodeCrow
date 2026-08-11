package org.rostilos.codecrow.webserver.publicshare;

import java.util.Optional;

/** Resolves one internal resource type into its explicitly sanitized public DTO. */
public interface PublicShareResourceProvider {

    String resourceType();

    Optional<?> getPublicPreview(String resourceKey);
}


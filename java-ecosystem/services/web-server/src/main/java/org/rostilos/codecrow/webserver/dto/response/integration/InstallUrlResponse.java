package org.rostilos.codecrow.webserver.dto.response.integration;

/**
 * Response for app installation URL.
 */
public record InstallUrlResponse(
    String installUrl,
    String provider,
    String state
) {}

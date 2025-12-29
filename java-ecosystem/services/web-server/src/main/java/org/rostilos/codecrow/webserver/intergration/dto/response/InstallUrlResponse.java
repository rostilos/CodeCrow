package org.rostilos.codecrow.webserver.intergration.dto.response;

/**
 * Response for app installation URL.
 */
public record InstallUrlResponse(
    String installUrl,
    String provider,
    String state
) {}

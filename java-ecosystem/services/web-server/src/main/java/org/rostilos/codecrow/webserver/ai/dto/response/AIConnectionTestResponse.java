package org.rostilos.codecrow.webserver.ai.dto.response;

public record AIConnectionTestResponse(
        boolean success,
        String message,
        int statusCode,
        long latencyMs
) {
}

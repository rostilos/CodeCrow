package org.rostilos.codecrow.webserver.dto.error;

import org.springframework.http.HttpStatus;

import java.time.Instant;

public class ErrorResponse {
    private final String message;
    private final int status;
    private final Instant timestamp;

    public ErrorResponse(String message, HttpStatus status) {
        this.message = message;
        this.status = status.value();
        this.timestamp = Instant.now();
    }

    public String getMessage() {
        return message;
    }

    public int getStatus() {
        return status;
    }

    public Instant getTimestamp() {
        return timestamp;
    }
}

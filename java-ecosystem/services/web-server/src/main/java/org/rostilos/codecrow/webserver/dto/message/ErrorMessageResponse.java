package org.rostilos.codecrow.webserver.dto.message;

import org.springframework.http.HttpStatus;

import java.time.Instant;

public class ErrorMessageResponse extends MessageResponse {
    private final int status;
    private final Instant timestamp;

    public ErrorMessageResponse(String message, HttpStatus status) {
        super(message);
        this.status = status.value();
        this.timestamp = Instant.now();
    }

    public int getStatus() {
        return status;
    }

    public Instant getTimestamp() {
        return timestamp;
    }
}

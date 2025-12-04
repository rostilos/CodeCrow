package org.rostilos.codecrow.webserver.exception;

public class TwoFactorRequiredException extends RuntimeException {
    public TwoFactorRequiredException(String message) {
        super(message);
    }
}

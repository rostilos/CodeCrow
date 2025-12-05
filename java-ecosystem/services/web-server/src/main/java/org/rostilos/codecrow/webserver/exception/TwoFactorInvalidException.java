package org.rostilos.codecrow.webserver.exception;

public class TwoFactorInvalidException extends RuntimeException {
    public TwoFactorInvalidException(String message) {
        super(message);
    }
}

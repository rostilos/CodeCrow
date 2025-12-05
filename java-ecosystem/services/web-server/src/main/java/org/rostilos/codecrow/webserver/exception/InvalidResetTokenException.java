package org.rostilos.codecrow.webserver.exception;

public class InvalidResetTokenException extends RuntimeException {
    
    public InvalidResetTokenException(String message) {
        super(message);
    }
    
    public InvalidResetTokenException(String message, Throwable cause) {
        super(message, cause);
    }
}

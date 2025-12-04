package org.rostilos.codecrow.webserver.exception;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.ResponseStatus;

/**
 * Exception for VCS integration errors.
 */
@ResponseStatus(HttpStatus.BAD_REQUEST)
public class IntegrationException extends RuntimeException {
    
    private final String errorCode;
    
    public IntegrationException(String message) {
        super(message);
        this.errorCode = "INTEGRATION_ERROR";
    }
    
    public IntegrationException(String message, String errorCode) {
        super(message);
        this.errorCode = errorCode;
    }
    
    public IntegrationException(String message, Throwable cause) {
        super(message, cause);
        this.errorCode = "INTEGRATION_ERROR";
    }
    
    public String getErrorCode() {
        return errorCode;
    }
}

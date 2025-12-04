package org.rostilos.codecrow.vcsclient;

/**
 * Exception thrown when VCS client operations fail.
 */
public class VcsClientException extends RuntimeException {
    
    public VcsClientException(String message) {
        super(message);
    }
    
    public VcsClientException(String message, Throwable cause) {
        super(message, cause);
    }
}

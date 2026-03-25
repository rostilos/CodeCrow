package org.rostilos.codecrow.taskmanagement;

/**
 * Exception thrown by task management client operations.
 * Wraps provider-specific errors into a unified exception hierarchy.
 */
public class TaskManagementException extends RuntimeException {

    private final int statusCode;
    private final String providerMessage;

    public TaskManagementException(String message) {
        super(message);
        this.statusCode = 0;
        this.providerMessage = null;
    }

    public TaskManagementException(String message, Throwable cause) {
        super(message, cause);
        this.statusCode = 0;
        this.providerMessage = null;
    }

    public TaskManagementException(String message, int statusCode, String providerMessage) {
        super(message);
        this.statusCode = statusCode;
        this.providerMessage = providerMessage;
    }

    public TaskManagementException(String message, int statusCode, String providerMessage, Throwable cause) {
        super(message, cause);
        this.statusCode = statusCode;
        this.providerMessage = providerMessage;
    }

    /**
     * HTTP status code from the provider, or 0 if not applicable.
     */
    public int getStatusCode() {
        return statusCode;
    }

    /**
     * Raw error message from the provider, or {@code null}.
     */
    public String getProviderMessage() {
        return providerMessage;
    }

    /**
     * @return {@code true} if the error indicates invalid credentials or insufficient permissions.
     */
    public boolean isAuthError() {
        return statusCode == 401 || statusCode == 403;
    }

    /**
     * @return {@code true} if the error is a rate limit (HTTP 429).
     */
    public boolean isRateLimited() {
        return statusCode == 429;
    }
}

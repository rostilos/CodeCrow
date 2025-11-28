package org.rostilos.codecrow.webserver.exception.user;

public class UserIdNotFoundException extends RuntimeException {
    public UserIdNotFoundException(String message) {
        super(message);
    }

    public UserIdNotFoundException(String msg, Throwable cause) {
        super(msg, cause);
    }
}

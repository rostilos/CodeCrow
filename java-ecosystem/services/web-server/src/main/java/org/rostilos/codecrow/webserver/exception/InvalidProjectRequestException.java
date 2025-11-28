package org.rostilos.codecrow.webserver.exception;

public class InvalidProjectRequestException extends RuntimeException{
    public InvalidProjectRequestException(String message) {
        super(message);
    }
}

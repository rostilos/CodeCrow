package org.rostilos.codecrow.webserver.exception.comment;

/**
 * Exception thrown when a command is not recognized or not supported.
 */
public class UnknownCommandException extends CommentCommandException {
    
    private final String commandName;
    
    public UnknownCommandException(String commandName) {
        super("Unknown command: " + commandName, null, "UNKNOWN_COMMAND", false);
        this.commandName = commandName;
    }
    
    public UnknownCommandException(String commandName, String message) {
        super(message, null, "UNKNOWN_COMMAND", false);
        this.commandName = commandName;
    }
    
    public String getCommandName() {
        return commandName;
    }
}

package org.rostilos.codecrow.webserver.exception.comment;

/**
 * Exception thrown when command execution fails.
 */
public class CommandExecutionException extends CommentCommandException {
    
    private final String commandName;
    private final String executionPhase;
    
    public CommandExecutionException(String commandName, String message, Throwable cause) {
        super(message, cause, "COMMAND_EXECUTION_FAILED", true);
        this.commandName = commandName;
        this.executionPhase = "UNKNOWN";
    }
    
    public CommandExecutionException(String commandName, String executionPhase, String message, Throwable cause) {
        super(message, cause, "COMMAND_EXECUTION_FAILED", true);
        this.commandName = commandName;
        this.executionPhase = executionPhase;
    }
    
    public CommandExecutionException(String commandName, String executionPhase, String message) {
        super(message, null, "COMMAND_EXECUTION_FAILED", false);
        this.commandName = commandName;
        this.executionPhase = executionPhase;
    }
    
    public String getCommandName() {
        return commandName;
    }
    
    public String getExecutionPhase() {
        return executionPhase;
    }
    
    /**
     * Execution phases for tracking where failures occur.
     */
    public static class Phase {
        public static final String PARSING = "PARSING";
        public static final String VALIDATION = "VALIDATION";
        public static final String AUTHORIZATION = "AUTHORIZATION";
        public static final String RATE_CHECK = "RATE_CHECK";
        public static final String FETCH_DATA = "FETCH_DATA";
        public static final String AI_PROCESSING = "AI_PROCESSING";
        public static final String RESPONSE_BUILD = "RESPONSE_BUILD";
        public static final String POST_RESPONSE = "POST_RESPONSE";
        
        private Phase() {}
    }
}

package org.rostilos.codecrow.webserver.exception.comment;

/**
 * Exception thrown when a user is not authorized to execute a command.
 */
public class CommandUnauthorizedException extends CommentCommandException {
    
    private final String commandName;
    private final String vcsUsername;
    private final String requiredRole;
    
    public CommandUnauthorizedException(String commandName, String vcsUsername, String requiredRole) {
        super(String.format("User '%s' is not authorized to execute command '%s'. Required role: %s", 
              vcsUsername, commandName, requiredRole), 
              null, "COMMAND_UNAUTHORIZED", false);
        this.commandName = commandName;
        this.vcsUsername = vcsUsername;
        this.requiredRole = requiredRole;
    }
    
    public CommandUnauthorizedException(String vcsUsername) {
        super(String.format("User '%s' is not authorized to execute commands on this project", vcsUsername), 
              null, "COMMAND_UNAUTHORIZED", false);
        this.commandName = null;
        this.vcsUsername = vcsUsername;
        this.requiredRole = null;
    }
    
    public String getCommandName() {
        return commandName;
    }
    
    public String getVcsUsername() {
        return vcsUsername;
    }
    
    public String getRequiredRole() {
        return requiredRole;
    }
}

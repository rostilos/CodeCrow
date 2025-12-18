package org.rostilos.codecrow.webserver.exception.comment;

/**
 * Exception thrown when a command is disabled for the project.
 */
public class CommandDisabledException extends CommentCommandException {
    
    private final String commandName;
    private final Long projectId;
    
    public CommandDisabledException(String commandName, Long projectId) {
        super("Command '" + commandName + "' is disabled for this project", 
              null, "COMMAND_DISABLED", false);
        this.commandName = commandName;
        this.projectId = projectId;
    }
    
    public CommandDisabledException(Long projectId) {
        super("Comment commands are disabled for this project", 
              null, "COMMANDS_DISABLED", false);
        this.commandName = null;
        this.projectId = projectId;
    }
    
    public String getCommandName() {
        return commandName;
    }
    
    public Long getProjectId() {
        return projectId;
    }
}

package org.rostilos.codecrow.core.model.project.config;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

/**
 * Configuration for comment-triggered commands (/codecrow analyze, summarize, ask).
 * Only available when project is connected via App integration (Bitbucket App or GitHub App).
 * 
 * @param enabled Whether comment commands are enabled for this project
 * @param rateLimit Maximum number of commands allowed per rate limit window
 * @param rateLimitWindowMinutes Duration of the rate limit window in minutes
 * @param allowPublicRepoCommands Whether to allow commands on public repositories (requires high privilege users)
 * @param allowedCommands List of allowed command types (null = all commands allowed)
 * @param authorizationMode Controls who can execute commands (default: ANYONE)
 * @param allowPrAuthor If true, PR author can always execute commands regardless of mode
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record CommentCommandsConfig(
    @JsonProperty("enabled") boolean enabled,
    @JsonProperty("rateLimit") Integer rateLimit,
    @JsonProperty("rateLimitWindowMinutes") Integer rateLimitWindowMinutes,
    @JsonProperty("allowPublicRepoCommands") Boolean allowPublicRepoCommands,
    @JsonProperty("allowedCommands") List<String> allowedCommands,
    @JsonProperty("authorizationMode") CommandAuthorizationMode authorizationMode,
    @JsonProperty("allowPrAuthor") Boolean allowPrAuthor
) {
    public static final int DEFAULT_RATE_LIMIT = 10;
    public static final int DEFAULT_RATE_LIMIT_WINDOW_MINUTES = 60;
    public static final CommandAuthorizationMode DEFAULT_AUTHORIZATION_MODE = CommandAuthorizationMode.ANYONE;
    
    /**
     * Default constructor - commands are ENABLED by default with ANYONE authorization.
     */
    public CommentCommandsConfig() {
        this(true, DEFAULT_RATE_LIMIT, DEFAULT_RATE_LIMIT_WINDOW_MINUTES, false, null, 
             DEFAULT_AUTHORIZATION_MODE, true);
    }
    
    public CommentCommandsConfig(boolean enabled) {
        this(enabled, DEFAULT_RATE_LIMIT, DEFAULT_RATE_LIMIT_WINDOW_MINUTES, false, null,
             DEFAULT_AUTHORIZATION_MODE, true);
    }
    
    /**
     * Get the effective rate limit (defaults to DEFAULT_RATE_LIMIT if null).
     */
    public int getEffectiveRateLimit() {
        return rateLimit != null ? rateLimit : DEFAULT_RATE_LIMIT;
    }
    
    /**
     * Get the effective rate limit window in minutes (defaults to DEFAULT_RATE_LIMIT_WINDOW_MINUTES if null).
     */
    public int getEffectiveRateLimitWindowMinutes() {
        return rateLimitWindowMinutes != null ? rateLimitWindowMinutes : DEFAULT_RATE_LIMIT_WINDOW_MINUTES;
    }
    
    /**
     * Check if a specific command type is allowed.
     * @param commandType The command type (e.g., "analyze", "summarize", "ask")
     * @return true if the command is allowed (null allowedCommands means all are allowed)
     */
    public boolean isCommandAllowed(String commandType) {
        return allowedCommands == null || allowedCommands.isEmpty() || allowedCommands.contains(commandType);
    }
    
    /**
     * Check if commands are allowed on public repositories.
     */
    public boolean allowsPublicRepoCommands() {
        return allowPublicRepoCommands != null && allowPublicRepoCommands;
    }
    
    /**
     * Get the effective authorization mode.
     */
    public CommandAuthorizationMode getEffectiveAuthorizationMode() {
        return authorizationMode != null ? authorizationMode : DEFAULT_AUTHORIZATION_MODE;
    }
    
    /**
     * Check if PR author is always allowed to execute commands.
     */
    public boolean isPrAuthorAllowed() {
        return allowPrAuthor == null || allowPrAuthor;
    }
}

package org.rostilos.codecrow.webserver.project.dto.request;

import java.util.List;

/**
 * Request DTO for updating comment commands configuration.
 * <p>
 * Comment commands allow users to trigger analysis via PR comments:
 * - /codecrow analyze - trigger PR analysis
 * - /codecrow summarize - generate PR summary with diagrams
 * - /codecrow ask [question] - ask questions about the analysis
 */
public record UpdateCommentCommandsConfigRequest(
        /**
         * Whether comment commands are enabled for this project.
         * Requires App-based VCS connection (not OAuth).
         */
        Boolean enabled,
        
        /**
         * Maximum number of commands allowed per rate limit window.
         * Default: 10
         */
        Integer rateLimit,
        
        /**
         * Rate limit window in minutes.
         * Default: 60
         */
        Integer rateLimitWindowMinutes,
        
        /**
         * Whether to allow comment commands on public repositories.
         * If false, only authorized users can trigger commands on public repos.
         * Default: false
         */
        Boolean allowPublicRepoCommands,
        
        /**
         * List of allowed commands. Valid values: "analyze", "summarize", "ask"
         * If null or empty, all commands are disabled.
         */
        List<String> allowedCommands
) {
    public List<String> validatedAllowedCommands() {
        if (allowedCommands == null || allowedCommands.isEmpty()) {
            return List.of();
        }
        
        var validCommands = List.of("analyze", "summarize", "ask");
        return allowedCommands.stream()
                .filter(validCommands::contains)
                .toList();
    }
}

package org.rostilos.codecrow.pipelineagent.generic.utils;

public class CommentPlaceholders {
    /** Comment markers for CodeCrow command responses */
    public static final String CODECROW_COMMAND_MARKER = "<!-- codecrow-command-response -->";
    public static final String CODECROW_SUMMARY_MARKER = "<!-- codecrow-summary -->";
    public static final String CODECROW_REVIEW_MARKER = "<!-- codecrow-review -->";
    
    /** Placeholder messages for commands */
    public static final String PLACEHOLDER_ANALYZE = """
        🔄 **CodeCrow is analyzing this PR...**
        
        This may take a few minutes depending on the size of the changes.
        I'll update this comment with the results when the analysis is complete.
        """;
    
    public static final String PLACEHOLDER_SUMMARIZE = """
        🔄 **CodeCrow is generating a summary...**
        
        I'm analyzing the changes and creating diagrams.
        This comment will be updated with the summary when ready.
        """;
    
    public static final String PLACEHOLDER_REVIEW = """
        🔄 **CodeCrow is reviewing this PR...**
        
        I'm examining the code changes for potential issues.
        This comment will be updated with the review results when complete.
        """;
    
    public static final String PLACEHOLDER_ASK = """
        🔄 **CodeCrow is processing your question...**
        
        I'm analyzing the context to provide a helpful answer.
        """;
    
    public static final String PLACEHOLDER_DEFAULT = """
        🔄 **CodeCrow is processing...**
        
        Please wait while I complete this task.
        """;

    /**
     * Get the placeholder message for a command type.
     */
    public static String getPlaceholderMessage(String commandType) {
        if (commandType == null) {
            return PLACEHOLDER_DEFAULT;
        }
        return switch (commandType.toLowerCase()) {
            case "analyze" -> PLACEHOLDER_ANALYZE;
            case "summarize" -> PLACEHOLDER_SUMMARIZE;
            case "review" -> PLACEHOLDER_REVIEW;
            case "ask" -> PLACEHOLDER_ASK;
            default -> PLACEHOLDER_DEFAULT;
        };
    }
    
    /**
     * Get the comment marker for a command type.
     */
    public static String getMarkerForCommandType(String commandType) {
        if (commandType == null) {
            return CODECROW_COMMAND_MARKER;
        }
        return switch (commandType.toLowerCase()) {
            case "summarize" -> CODECROW_SUMMARY_MARKER;
            case "review" -> CODECROW_REVIEW_MARKER;
            default -> CODECROW_COMMAND_MARKER;
        };
    }
}

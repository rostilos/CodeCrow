package org.rostilos.codecrow.core.model.codeanalysis;

/**
 * Describes the granularity of an issue's location within a file.
 * <p>
 * The scope affects how the issue is displayed, tracked, and reconciled:
 * <ul>
 *   <li>{@link #LINE} — Pinpointed to a single source line (most common). Deterministic
 *       resolution: if the line hash disappears from the file, the issue is auto-resolved.</li>
 *   <li>{@link #BLOCK} — Spans a contiguous range of lines (e.g. a conditional block,
 *       loop body). Uses {@code lineNumber} as start and {@code endLineNumber} as end.
 *       Deterministic resolution: auto-resolved only when ALL anchor lines are gone.</li>
 *   <li>{@link #FUNCTION} — Applies to an entire function/method. Uses {@code lineNumber}
 *       as the function start and {@code endLineNumber} as the function end.
 *       Deterministic resolution: auto-resolved only when ALL anchor lines are gone;
 *       otherwise delegates to AI reconciliation.</li>
 *   <li>{@link #FILE} — Architectural or structural concern that applies to the file as a
 *       whole (e.g. tight coupling, missing module boundaries). No specific line anchor.
 *       <b>Never</b> auto-resolved deterministically — always requires AI reconciliation
 *       or manual resolution.</li>
 * </ul>
 */
public enum IssueScope {

    LINE("Line", "Pinpointed to a single source line"),
    BLOCK("Block", "Spans a contiguous range of lines"),
    FUNCTION("Function", "Applies to an entire function or method"),
    FILE("File", "Architectural/structural concern for the whole file");

    private final String displayName;
    private final String description;

    IssueScope(String displayName, String description) {
        this.displayName = displayName;
        this.description = description;
    }

    public String getDisplayName() {
        return displayName;
    }

    public String getDescription() {
        return description;
    }

    /**
     * Parse an issue scope from the AI response string.
     * Returns {@code null} if the value is {@code null} or blank (caller should infer scope).
     */
    public static IssueScope fromString(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        String normalized = value.trim().toUpperCase().replace(" ", "_").replace("-", "_");
        for (IssueScope scope : values()) {
            if (scope.name().equals(normalized) || scope.displayName.equalsIgnoreCase(value.trim())) {
                return scope;
            }
        }
        return switch (normalized) {
            case "METHOD", "FN", "FUNC", "PROCEDURE" -> FUNCTION;
            case "RANGE", "MULTILINE", "MULTI_LINE", "LINES" -> BLOCK;
            case "SINGLE", "POINT", "PINPOINT" -> LINE;
            case "MODULE", "CLASS", "ARCHITECTURAL", "STRUCTURAL" -> FILE;
            default -> null;
        };
    }

    /**
     * Get all scope names and descriptions for use in LLM prompts.
     */
    public static String getAllScopesForPrompt() {
        StringBuilder sb = new StringBuilder();
        for (IssueScope scope : values()) {
            sb.append("- ").append(scope.name()).append(": ").append(scope.description).append("\n");
        }
        return sb.toString();
    }
}

package org.rostilos.codecrow.core.model.codeanalysis;

public enum IssueCategory {
    SECURITY("Security", "Security vulnerabilities, injection risks, authentication issues"),
    PERFORMANCE("Performance", "Performance bottlenecks, inefficient algorithms, resource leaks"),
    CODE_QUALITY("Code Quality", "Code smells, maintainability issues, complexity problems"),
    BUG_RISK("Bug Risk", "Potential bugs, edge cases, null pointer risks"),
    STYLE("Style", "Code style, formatting, naming conventions"),
    DOCUMENTATION("Documentation", "Missing or inadequate documentation"),
    BEST_PRACTICES("Best Practices", "Violations of language/framework best practices"),
    ERROR_HANDLING("Error Handling", "Improper exception handling, missing error checks"),
    TESTING("Testing", "Test coverage issues, untestable code"),
    ARCHITECTURE("Architecture", "Design issues, coupling problems, SOLID violations");

    private final String displayName;
    private final String description;

    IssueCategory(String displayName, String description) {
        this.displayName = displayName;
        this.description = description;
    }

    public String getDisplayName() {
        return displayName;
    }

    public String getDescription() {
        return description;
    }

    public static IssueCategory fromString(String value) {
        if (value == null || value.isBlank()) {
            return CODE_QUALITY;
        }
        
        String normalized = value.trim().toUpperCase().replace(" ", "_").replace("-", "_");
        
        for (IssueCategory category : values()) {
            if (category.name().equals(normalized) || 
                category.displayName.equalsIgnoreCase(value.trim())) {
                return category;
            }
        }
        
        return switch (normalized) {
            case "QUALITY", "CODE_SMELL", "MAINTAINABILITY" -> CODE_QUALITY;
            case "BUG", "BUGS", "ERROR", "DEFECT" -> BUG_RISK;
            case "PERF", "OPTIMIZATION", "EFFICIENCY" -> PERFORMANCE;
            case "SEC", "VULNERABILITY", "AUTH" -> SECURITY;
            case "FORMAT", "FORMATTING", "NAMING" -> STYLE;
            case "DOCS", "COMMENT", "COMMENTS" -> DOCUMENTATION;
            case "EXCEPTION", "EXCEPTIONS", "ERROR_HANDLE" -> ERROR_HANDLING;
            case "TEST", "TESTS", "COVERAGE" -> TESTING;
            case "DESIGN", "STRUCTURE", "SOLID" -> ARCHITECTURE;
            case "PRACTICE", "PRACTICES", "CONVENTION" -> BEST_PRACTICES;
            default -> CODE_QUALITY;
        };
    }

    public static String getAllCategoriesForPrompt() {
        StringBuilder sb = new StringBuilder();
        for (IssueCategory cat : values()) {
            sb.append("- ").append(cat.name()).append(": ").append(cat.description).append("\n");
        }
        return sb.toString();
    }
}

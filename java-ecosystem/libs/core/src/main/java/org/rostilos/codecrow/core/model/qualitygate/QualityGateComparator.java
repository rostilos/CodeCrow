package org.rostilos.codecrow.core.model.qualitygate;

/**
 * Comparators for quality gate conditions.
 */
public enum QualityGateComparator {
    GREATER_THAN(">", "greater than"),
    GREATER_THAN_OR_EQUAL(">=", "greater than or equal to"),
    LESS_THAN("<", "less than"),
    LESS_THAN_OR_EQUAL("<=", "less than or equal to"),
    EQUAL("==", "equal to"),
    NOT_EQUAL("!=", "not equal to");

    private final String symbol;
    private final String description;

    QualityGateComparator(String symbol, String description) {
        this.symbol = symbol;
        this.description = description;
    }

    public String getSymbol() { return symbol; }
    public String getDescription() { return description; }
}

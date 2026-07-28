package org.rostilos.codecrow.plugins;

/**
 * Neutral host policy for deciding how a selected plugin's repository file is
 * allowed to participate in analysis.
 */
public enum FileDisposition {
    FULL("full"),
    ARCHITECTURE_ONLY("architecture-only"),
    GENERATED("generated"),
    EXCLUDED("excluded");

    private final String value;

    FileDisposition(String value) {
        this.value = value;
    }

    public String value() {
        return value;
    }
}

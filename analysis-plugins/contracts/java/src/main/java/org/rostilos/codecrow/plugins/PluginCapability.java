package org.rostilos.codecrow.plugins;

public enum PluginCapability {
    CALIBRATION("calibration"),
    CANDIDATE_RECIPE("candidate-recipe"),
    CONTEXT("context"),
    FILE_POLICY("file-policy"),
    GRAPH("graph"),
    INDEX("index"),
    PLANNING("planning"),
    PROMPT("prompt"),
    SYNTAX("syntax"),
    VALIDATION("validation");

    private final String value;

    PluginCapability(String value) {
        this.value = value;
    }

    public String value() {
        return value;
    }

    public static PluginCapability fromValue(String value) {
        for (PluginCapability capability : values()) {
            if (capability.value.equals(value)) return capability;
        }
        throw new IllegalArgumentException("unknown plugin capability: " + value);
    }
}

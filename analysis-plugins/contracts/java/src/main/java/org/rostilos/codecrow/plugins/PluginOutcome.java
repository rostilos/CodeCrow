package org.rostilos.codecrow.plugins;

public record PluginOutcome<T>(OutcomeStatus status, T value, PluginDiagnostic diagnostic) {
    public PluginOutcome {
        if (status == null) throw new IllegalArgumentException("outcome status is required");
        switch (status) {
            case HANDLED -> {
                if (value == null || diagnostic != null) {
                    throw new IllegalArgumentException("handled outcome requires a value and no diagnostic");
                }
            }
            case ABSTAINED -> {
                if (value != null || diagnostic != null) {
                    throw new IllegalArgumentException("abstained outcome carries no value or diagnostic");
                }
            }
            case FAILED -> {
                if (value != null || diagnostic == null) {
                    throw new IllegalArgumentException("failed outcome requires a diagnostic and no value");
                }
            }
        }
    }

    public static <T> PluginOutcome<T> handled(T value) {
        return new PluginOutcome<>(OutcomeStatus.HANDLED, value, null);
    }

    public static <T> PluginOutcome<T> abstained() {
        return new PluginOutcome<>(OutcomeStatus.ABSTAINED, null, null);
    }

    public static <T> PluginOutcome<T> failed(PluginDiagnostic diagnostic) {
        return new PluginOutcome<>(OutcomeStatus.FAILED, null, diagnostic);
    }
}

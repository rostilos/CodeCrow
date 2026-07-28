package org.rostilos.codecrow.plugins;

public record PluginDiagnostic(String code, String message, String pluginId) {
    public PluginDiagnostic {
        code = PluginValues.requireNonBlank(code, "diagnostic code");
        message = PluginValues.requireNonBlank(message, "diagnostic message");
        if (pluginId != null) PluginValues.requirePluginId(pluginId, "plugin id");
    }
}

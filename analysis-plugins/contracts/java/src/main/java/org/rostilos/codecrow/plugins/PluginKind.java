package org.rostilos.codecrow.plugins;

public enum PluginKind {
    LANGUAGE("language"),
    FRAMEWORK("framework"),
    DOMAIN("domain");

    private final String value;

    PluginKind(String value) {
        this.value = value;
    }

    public String value() {
        return value;
    }

    public static PluginKind fromValue(String value) {
        for (PluginKind kind : values()) {
            if (kind.value.equals(value)) return kind;
        }
        throw new IllegalArgumentException("unknown plugin kind: " + value);
    }
}

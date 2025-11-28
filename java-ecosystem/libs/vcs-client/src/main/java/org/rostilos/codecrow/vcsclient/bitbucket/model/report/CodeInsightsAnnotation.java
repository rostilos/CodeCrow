package org.rostilos.codecrow.vcsclient.bitbucket.model.report;

import com.fasterxml.jackson.annotation.JsonProperty;

public class CodeInsightsAnnotation {
    private final int line;
    private final String message;
    private final String path;
    private final String severity;

    public CodeInsightsAnnotation(int line, String message, String path, String severity) {
        this.line = line;
        this.message = message;
        this.path = path;
        this.severity = severity;
    }

    @JsonProperty("line")
    public int getLine() {
        return line;
    }

    @JsonProperty("message")
    public String getMessage() {
        return message;
    }

    @JsonProperty("path")
    public String getPath() {
        return path;
    }

    @JsonProperty("severity")
    public String getSeverity() {
        return severity;
    }

}

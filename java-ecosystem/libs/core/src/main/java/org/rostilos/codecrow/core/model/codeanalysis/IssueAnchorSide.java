package org.rostilos.codecrow.core.model.codeanalysis;

/** The source revision whose line coordinates identify a finding. */
public enum IssueAnchorSide {
    HEAD, BASE;
    public static IssueAnchorSide fromString(String value) {
        return "BASE".equalsIgnoreCase(value) ? BASE : HEAD;
    }
}

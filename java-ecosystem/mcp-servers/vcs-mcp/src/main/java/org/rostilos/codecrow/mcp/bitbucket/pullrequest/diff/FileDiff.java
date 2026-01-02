package org.rostilos.codecrow.mcp.bitbucket.pullrequest.diff;


import com.fasterxml.jackson.annotation.JsonCreator;

public class FileDiff {
    private String filePath;
    private DiffType diffType;
    private String rawContent;
    private String sha;
    private String changes;

    public enum DiffType {
        ADDED,
        MODIFIED,
        REMOVED,
        RENAMED;

        @JsonCreator
        public static DiffType fromValue(String value) {
            return DiffType.valueOf(value.toUpperCase());
        }
    }

    public String getFilePath() {
        return filePath;
    }

    public void setFilePath(String filePath) {
        this.filePath = filePath;
    }

    public DiffType getDiffType() {
        return diffType;
    }

    public void setDiffType(DiffType diffType) {
        this.diffType = diffType;
    }

    public String getRawContent() {
        return rawContent;
    }

    public void setRawContent(String rawContent) {
        this.rawContent = rawContent;
    }

    public String getSha() {
        return sha;
    }

    public void setSha(String sha) {
        this.sha = sha;
    }

    public String getChanges() {
        return changes;
    }

    public void setChanges(String changes) {
        this.changes = changes;
    }
}

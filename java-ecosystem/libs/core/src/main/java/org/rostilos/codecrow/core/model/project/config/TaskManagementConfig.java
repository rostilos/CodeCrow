package org.rostilos.codecrow.core.model.project.config;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * Project-level task-management binding shared by task-aware PR analysis and
 * QA auto-documentation.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record TaskManagementConfig(
        @JsonProperty("taskManagementConnectionId") Long taskManagementConnectionId,
        @JsonProperty("taskIdPattern") String taskIdPattern,
        @JsonProperty("taskIdSource") TaskIdSource taskIdSource
) {
    public static final String DEFAULT_TASK_ID_PATTERN = "[A-Z][A-Z0-9]+-\\d+";

    public enum TaskIdSource {
        BRANCH_NAME,
        PR_TITLE,
        PR_DESCRIPTION
    }

    public TaskManagementConfig() {
        this(null, DEFAULT_TASK_ID_PATTERN, TaskIdSource.BRANCH_NAME);
    }

    public String effectiveTaskIdPattern() {
        return taskIdPattern != null && !taskIdPattern.isBlank()
                ? taskIdPattern
                : DEFAULT_TASK_ID_PATTERN;
    }

    public TaskIdSource effectiveTaskIdSource() {
        return taskIdSource != null ? taskIdSource : TaskIdSource.BRANCH_NAME;
    }

    public boolean hasBoundConnection() {
        return taskManagementConnectionId != null;
    }

    public boolean isFullyConfigured() {
        return hasBoundConnection()
                && effectiveTaskIdPattern() != null
                && !effectiveTaskIdPattern().isBlank();
    }

    static TaskManagementConfig fromLegacyQaAutoDoc(QaAutoDocConfig qaAutoDoc) {
        if (qaAutoDoc == null) {
            return new TaskManagementConfig();
        }
        TaskIdSource source = qaAutoDoc.taskIdSource() != null
                ? TaskIdSource.valueOf(qaAutoDoc.taskIdSource().name())
                : TaskIdSource.BRANCH_NAME;
        return new TaskManagementConfig(
                qaAutoDoc.taskManagementConnectionId(),
                qaAutoDoc.taskIdPattern(),
                source
        );
    }
}

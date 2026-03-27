package org.rostilos.codecrow.core.model.project.config;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * Configuration for QA auto-documentation feature within {@link ProjectConfig}.
 * <p>
 * When enabled, the system automatically generates a QA-oriented summary
 * of PR changes and posts it as a comment on the linked task (e.g., Jira ticket).
 * </p>
 *
 * <h3>Template Modes</h3>
 * <ul>
 *   <li>{@code RAW} — No template; raw LLM response as-is.</li>
 *   <li>{@code BASE} — Hardcoded default template (Summary, Changed Areas, What To Test, Edge Cases).</li>
 *   <li>{@code CUSTOM} — User-provided template with safe {@code {placeholder}} substitution.</li>
 * </ul>
 *
 * @param enabled                       whether QA auto-documentation is active for this project
 * @param taskManagementConnectionId    ID of the workspace-level task management connection to use
 * @param taskIdPattern                 regex pattern to extract task ID from PR metadata (e.g. {@code [A-Z]+-\d+})
 * @param taskIdSource                  where to extract the task ID from (branch name, PR title, PR description)
 * @param templateMode                  which template mode to use for generating the QA document
 * @param customTemplate                user-defined template text (only used when templateMode = CUSTOM, max 5000 chars)
 * @param outputLanguage                the language for generated QA documentation (e.g. "English", "Ukrainian"). Defaults to English.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record QaAutoDocConfig(
        @JsonProperty("enabled") boolean enabled,
        @JsonProperty("taskManagementConnectionId") Long taskManagementConnectionId,
        @JsonProperty("taskIdPattern") String taskIdPattern,
        @JsonProperty("taskIdSource") TaskIdSource taskIdSource,
        @JsonProperty("templateMode") TemplateMode templateMode,
        @JsonProperty("customTemplate") String customTemplate,
        @JsonProperty("outputLanguage") String outputLanguage
) {

    /** Maximum allowed length for custom templates. */
    public static final int MAX_CUSTOM_TEMPLATE_LENGTH = 5000;

    /** Default task ID regex pattern — matches standard Jira keys like WS-111, GR-2499. */
    public static final String DEFAULT_TASK_ID_PATTERN = "[A-Z][A-Z0-9]+-\\d+";

    /**
     * Where to extract the task ID from PR metadata.
     */
    public enum TaskIdSource {
        BRANCH_NAME,
        PR_TITLE,
        PR_DESCRIPTION
    }

    /**
     * How the QA documentation should be formatted.
     */
    public enum TemplateMode {
        /** No template — raw LLM response. */
        RAW,
        /** Hardcoded default template with structured sections. */
        BASE,
        /** User-provided template with safe placeholder substitution. */
        CUSTOM
    }

    /** Default disabled configuration. */
    public QaAutoDocConfig() {
        this(false, null, DEFAULT_TASK_ID_PATTERN, TaskIdSource.BRANCH_NAME, TemplateMode.BASE, null, null);
    }

    /** Convenience: enabled with base template. */
    public QaAutoDocConfig(boolean enabled, Long taskManagementConnectionId,
                           String taskIdPattern, TaskIdSource taskIdSource) {
        this(enabled, taskManagementConnectionId, taskIdPattern, taskIdSource, TemplateMode.BASE, null, null);
    }

    /**
     * @return effective task ID regex pattern (falls back to default if blank)
     */
    public String effectiveTaskIdPattern() {
        return taskIdPattern != null && !taskIdPattern.isBlank()
                ? taskIdPattern
                : DEFAULT_TASK_ID_PATTERN;
    }

    /**
     * @return effective task ID source (falls back to BRANCH_NAME if null)
     */
    public TaskIdSource effectiveTaskIdSource() {
        return taskIdSource != null ? taskIdSource : TaskIdSource.BRANCH_NAME;
    }

    /**
     * @return effective template mode (falls back to BASE if null)
     */
    public TemplateMode effectiveTemplateMode() {
        return templateMode != null ? templateMode : TemplateMode.BASE;
    }

    /**
     * @return effective output language (falls back to "English" if null or blank)
     */
    public String effectiveOutputLanguage() {
        return outputLanguage != null && !outputLanguage.isBlank() ? outputLanguage : "English";
    }

    /**
     * @return {@code true} if the configuration is fully set up (has a connection and pattern)
     */
    public boolean isFullyConfigured() {
        return enabled && taskManagementConnectionId != null
                && taskIdPattern != null && !taskIdPattern.isBlank();
    }
}

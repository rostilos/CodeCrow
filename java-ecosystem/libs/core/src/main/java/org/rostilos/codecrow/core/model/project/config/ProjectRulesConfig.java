package org.rostilos.codecrow.core.model.project.config;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;
import java.util.Objects;
import java.util.UUID;

/**
 * Configuration for custom project review rules.
 * <p>
 * Each rule tells the AI reviewer to either enforce a specific standard
 * ({@link RuleType#ENFORCE}) or suppress a known false-positive pattern
 * ({@link RuleType#SUPPRESS}).  Rules may optionally be scoped to specific
 * file patterns (glob syntax, e.g. {@code "src/main/**&#47;*.java"}).
 * <p>
 * Stored as part of {@link ProjectConfig#projectRules()}.
 *
 * @see RuleType
 * @see ProjectConfig
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record ProjectRulesConfig(
    @JsonProperty("rules") List<CustomRule> rules
) {
    /**
     * Default (empty) configuration.
     */
    public ProjectRulesConfig() {
        this(List.of());
    }

    /**
     * A single custom review rule defined by the project administrator.
     *
     * @param id           Stable UUID so the frontend can reference individual rules.
     * @param title        Short human-readable label (e.g. "Require null-checks on API params").
     * @param description  Detailed instruction for the AI reviewer explaining what to look for
     *                     or what to suppress and why.
     * @param ruleType     {@link RuleType#ENFORCE} or {@link RuleType#SUPPRESS}.
     * @param filePatterns Optional glob patterns limiting the rule's scope.
     *                     {@code null} or empty means the rule applies to all files.
     * @param enabled      Whether the rule is currently active.
     * @param priority     Ordering hint (lower = higher priority). Default 0.
     */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record CustomRule(
        @JsonProperty("id") String id,
        @JsonProperty("title") String title,
        @JsonProperty("description") String description,
        @JsonProperty("ruleType") RuleType ruleType,
        @JsonProperty("filePatterns") List<String> filePatterns,
        @JsonProperty("enabled") boolean enabled,
        @JsonProperty("priority") int priority
    ) {
        /**
         * Convenience constructor that auto-generates an ID.
         */
        public CustomRule(String title, String description, RuleType ruleType,
                          List<String> filePatterns, boolean enabled, int priority) {
            this(UUID.randomUUID().toString(), title, description, ruleType, filePatterns, enabled, priority);
        }

        /**
         * Returns {@code true} when this rule has no file-pattern restrictions
         * (i.e. it applies to every file in the project).
         */
        public boolean isGlobal() {
            return filePatterns == null || filePatterns.isEmpty();
        }
    }

    /**
     * Returns the number of rules (both enabled and disabled).
     */
    public int size() {
        return rules != null ? rules.size() : 0;
    }

    /**
     * Returns only the enabled rules, sorted by priority ascending.
     */
    public List<CustomRule> enabledRules() {
        if (rules == null) return List.of();
        return rules.stream()
                .filter(CustomRule::enabled)
                .sorted((a, b) -> Integer.compare(a.priority(), b.priority()))
                .toList();
    }

    /**
     * Serialise the enabled rules to a compact JSON string for inclusion
     * in the AI analysis request.  Returns {@code null} when there are no
     * enabled rules (so the caller can skip the field entirely).
     */
    public String toEnabledRulesJson() {
        List<CustomRule> enabled = enabledRules();
        if (enabled.isEmpty()) return null;

        // Build a lightweight JSON array by hand to avoid pulling in ObjectMapper here.
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < enabled.size(); i++) {
            CustomRule r = enabled.get(i);
            if (i > 0) sb.append(",");
            sb.append("{\"id\":\"").append(escapeJson(r.id())).append("\"");
            sb.append(",\"title\":\"").append(escapeJson(r.title())).append("\"");
            sb.append(",\"description\":\"").append(escapeJson(r.description())).append("\"");
            sb.append(",\"ruleType\":\"").append(r.ruleType().name()).append("\"");
            if (r.filePatterns() != null && !r.filePatterns().isEmpty()) {
                sb.append(",\"filePatterns\":[");
                for (int j = 0; j < r.filePatterns().size(); j++) {
                    if (j > 0) sb.append(",");
                    sb.append("\"").append(escapeJson(r.filePatterns().get(j))).append("\"");
                }
                sb.append("]");
            }
            sb.append("}");
        }
        sb.append("]");
        return sb.toString();
    }

    private static String escapeJson(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t");
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (o == null || getClass() != o.getClass()) return false;
        ProjectRulesConfig that = (ProjectRulesConfig) o;
        return Objects.equals(rules, that.rules);
    }

    @Override
    public int hashCode() {
        return Objects.hash(rules);
    }
}

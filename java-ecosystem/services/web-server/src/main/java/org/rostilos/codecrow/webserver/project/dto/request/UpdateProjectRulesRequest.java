package org.rostilos.codecrow.webserver.project.dto.request;

import org.rostilos.codecrow.core.model.project.config.RuleType;

import java.util.List;

/**
 * Request DTO for updating the custom project review rules.
 * <p>
 * This replaces the entire rules list atomically. The frontend is expected to
 * send the full list of rules on every save (add / edit / remove / reorder).
 */
public record UpdateProjectRulesRequest(
        /**
         * Full list of custom rules. Null or empty list clears all rules.
         */
        List<CustomRuleRequest> rules
) {
    /**
     * A single rule in the request payload.
     *
     * @param id           Optional rule ID. If null, a new UUID will be generated.
     * @param title        Short label for the rule (required, max 200 chars).
     * @param description  Detailed instruction for the AI reviewer (required, max 2000 chars).
     * @param ruleType     ENFORCE or SUPPRESS (required).
     * @param filePatterns Optional glob patterns to scope the rule (e.g. "src/main/**&#47;*.java").
     * @param enabled      Whether the rule is active. Default true.
     * @param priority     Ordering hint (lower = higher priority). Default 0.
     */
    public record CustomRuleRequest(
            String id,
            String title,
            String description,
            RuleType ruleType,
            List<String> filePatterns,
            Boolean enabled,
            Integer priority
    ) {
        /**
         * Returns {@code true} if enabled, defaulting to true when null.
         */
        public boolean isEnabled() {
            return enabled == null || enabled;
        }

        /**
         * Returns the priority, defaulting to 0 when null.
         */
        public int effectivePriority() {
            return priority != null ? priority : 0;
        }
    }
}

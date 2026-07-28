package org.rostilos.codecrow.analysisengine.util;

import java.util.Arrays;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * Test-only full-pipeline prompt capture selection.
 *
 * <p>The deployment switch is intentionally read at request time so an operator
 * can scope dry runs by project without introducing a project schema setting.
 */
public final class PromptDryRunMode {
    public static final String ENABLED_KEY = "ANALYSIS_PROMPT_DRY_RUN_ENABLED";
    public static final String PROJECT_IDS_KEY = "ANALYSIS_PROMPT_DRY_RUN_PROJECT_IDS";

    private PromptDryRunMode() {
    }

    public static boolean isEnabledForProject(Long projectId) {
        if (!isTrue(value(ENABLED_KEY))) {
            return false;
        }
        String configuredIds = value(PROJECT_IDS_KEY);
        if (configuredIds == null || configuredIds.isBlank()) {
            return true;
        }
        if (projectId == null) {
            return false;
        }
        Set<String> allowedIds = Arrays.stream(configuredIds.split(","))
                .map(String::trim)
                .filter(item -> !item.isEmpty())
                .collect(Collectors.toSet());
        return allowedIds.contains(String.valueOf(projectId));
    }

    private static String value(String key) {
        String property = System.getProperty(key);
        return property != null ? property : System.getenv(key);
    }

    private static boolean isTrue(String value) {
        return value != null && switch (value.trim().toLowerCase()) {
            case "1", "true", "yes", "on" -> true;
            default -> false;
        };
    }
}

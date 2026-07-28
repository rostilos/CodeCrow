package org.rostilos.codecrow.analysisengine.util;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class PromptDryRunModeTest {

    @AfterEach
    void clearProperties() {
        System.clearProperty(PromptDryRunMode.ENABLED_KEY);
        System.clearProperty(PromptDryRunMode.PROJECT_IDS_KEY);
    }

    @Test
    void disabledByDefault() {
        System.setProperty(PromptDryRunMode.ENABLED_KEY, "false");

        assertThat(PromptDryRunMode.isEnabledForProject(12L)).isFalse();
    }

    @Test
    void emptyProjectScopeSelectsEveryProject() {
        System.setProperty(PromptDryRunMode.ENABLED_KEY, "true");
        System.setProperty(PromptDryRunMode.PROJECT_IDS_KEY, "");

        assertThat(PromptDryRunMode.isEnabledForProject(12L)).isTrue();
        assertThat(PromptDryRunMode.isEnabledForProject(99L)).isTrue();
    }

    @Test
    void commaSeparatedScopeSelectsOnlyConfiguredProjects() {
        System.setProperty(PromptDryRunMode.ENABLED_KEY, "true");
        System.setProperty(PromptDryRunMode.PROJECT_IDS_KEY, "12, 44");

        assertThat(PromptDryRunMode.isEnabledForProject(12L)).isTrue();
        assertThat(PromptDryRunMode.isEnabledForProject(44L)).isTrue();
        assertThat(PromptDryRunMode.isEnabledForProject(99L)).isFalse();
    }
}

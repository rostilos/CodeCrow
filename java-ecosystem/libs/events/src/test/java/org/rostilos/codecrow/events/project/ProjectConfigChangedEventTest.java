package org.rostilos.codecrow.events.project;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class ProjectConfigChangedEventTest {

    @Test
    void testEventCreation_Created() {
        Object source = new Object();
        Long projectId = 1L;
        String projectName = "new-project";

        ProjectConfigChangedEvent event = new ProjectConfigChangedEvent(
                source, projectId, projectName,
                ProjectConfigChangedEvent.ChangeType.CREATED,
                null, null, null
        );

        assertThat(event.getSource()).isEqualTo(source);
        assertThat(event.getProjectId()).isEqualTo(projectId);
        assertThat(event.getProjectName()).isEqualTo(projectName);
        assertThat(event.getChangeType()).isEqualTo(ProjectConfigChangedEvent.ChangeType.CREATED);
        assertThat(event.getChangedField()).isNull();
        assertThat(event.getOldValue()).isNull();
        assertThat(event.getNewValue()).isNull();
        assertThat(event.getEventType()).isEqualTo("PROJECT_CONFIG_CHANGED");
        assertThat(event.getCorrelationId()).isEqualTo(event.getEventId());
    }

    @Test
    void testEventCreation_Updated() {
        ProjectConfigChangedEvent event = new ProjectConfigChangedEvent(
                this, 5L, "existing-project",
                ProjectConfigChangedEvent.ChangeType.UPDATED,
                "analysisEnabled", false, true
        );

        assertThat(event.getChangeType()).isEqualTo(ProjectConfigChangedEvent.ChangeType.UPDATED);
        assertThat(event.getChangedField()).isEqualTo("analysisEnabled");
        assertThat(event.getOldValue()).isEqualTo(false);
        assertThat(event.getNewValue()).isEqualTo(true);
    }

    @Test
    void testEventCreation_Deleted() {
        ProjectConfigChangedEvent event = new ProjectConfigChangedEvent(
                this, 10L, "deleted-project",
                ProjectConfigChangedEvent.ChangeType.DELETED,
                null, null, null
        );

        assertThat(event.getChangeType()).isEqualTo(ProjectConfigChangedEvent.ChangeType.DELETED);
    }

    @Test
    void testAnalysisConfigChanged() {
        ProjectConfigChangedEvent event = new ProjectConfigChangedEvent(
                this, 15L, "test-project",
                ProjectConfigChangedEvent.ChangeType.ANALYSIS_CONFIG_CHANGED,
                "branchPattern", "main", "main,develop"
        );

        assertThat(event.getChangeType()).isEqualTo(ProjectConfigChangedEvent.ChangeType.ANALYSIS_CONFIG_CHANGED);
        assertThat(event.getChangedField()).isEqualTo("branchPattern");
        assertThat(event.getOldValue()).isEqualTo("main");
        assertThat(event.getNewValue()).isEqualTo("main,develop");
    }

    @Test
    void testRagConfigChanged() {
        ProjectConfigChangedEvent event = new ProjectConfigChangedEvent(
                this, 20L, "rag-project",
                ProjectConfigChangedEvent.ChangeType.RAG_CONFIG_CHANGED,
                "ragEnabled", false, true
        );

        assertThat(event.getChangeType()).isEqualTo(ProjectConfigChangedEvent.ChangeType.RAG_CONFIG_CHANGED);
        assertThat(event.getChangedField()).isEqualTo("ragEnabled");
    }

    @Test
    void testQualityGateChanged() {
        ProjectConfigChangedEvent event = new ProjectConfigChangedEvent(
                this, 25L, "quality-project",
                ProjectConfigChangedEvent.ChangeType.QUALITY_GATE_CHANGED,
                "maxCriticalIssues", 10, 5
        );

        assertThat(event.getChangeType()).isEqualTo(ProjectConfigChangedEvent.ChangeType.QUALITY_GATE_CHANGED);
        assertThat(event.getChangedField()).isEqualTo("maxCriticalIssues");
        assertThat(event.getOldValue()).isEqualTo(10);
        assertThat(event.getNewValue()).isEqualTo(5);
    }

    @Test
    void testChangeType_AllValues() {
        assertThat(ProjectConfigChangedEvent.ChangeType.values()).containsExactly(
                ProjectConfigChangedEvent.ChangeType.CREATED,
                ProjectConfigChangedEvent.ChangeType.UPDATED,
                ProjectConfigChangedEvent.ChangeType.DELETED,
                ProjectConfigChangedEvent.ChangeType.ANALYSIS_CONFIG_CHANGED,
                ProjectConfigChangedEvent.ChangeType.RAG_CONFIG_CHANGED,
                ProjectConfigChangedEvent.ChangeType.QUALITY_GATE_CHANGED
        );
    }

    @Test
    void testEventType() {
        ProjectConfigChangedEvent event = new ProjectConfigChangedEvent(
                this, 1L, "project",
                ProjectConfigChangedEvent.ChangeType.UPDATED,
                "field", null, null
        );

        assertThat(event.getEventType()).isEqualTo("PROJECT_CONFIG_CHANGED");
    }
}

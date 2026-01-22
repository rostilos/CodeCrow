package org.rostilos.codecrow.core.model.qualitygate;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("QualityGateMetric")
class QualityGateMetricTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        QualityGateMetric[] values = QualityGateMetric.values();
        
        assertThat(values).hasSize(3);
        assertThat(values).contains(
                QualityGateMetric.ISSUES_BY_SEVERITY,
                QualityGateMetric.NEW_ISSUES,
                QualityGateMetric.ISSUES_BY_CATEGORY
        );
    }

    @Test
    @DisplayName("getDisplayName should return correct display name")
    void getDisplayNameShouldReturnCorrectDisplayName() {
        assertThat(QualityGateMetric.ISSUES_BY_SEVERITY.getDisplayName()).isEqualTo("Issues by Severity");
        assertThat(QualityGateMetric.NEW_ISSUES.getDisplayName()).isEqualTo("New Issues");
        assertThat(QualityGateMetric.ISSUES_BY_CATEGORY.getDisplayName()).isEqualTo("Issues by Category");
    }

    @Test
    @DisplayName("getDescription should return correct description")
    void getDescriptionShouldReturnCorrectDescription() {
        assertThat(QualityGateMetric.ISSUES_BY_SEVERITY.getDescription()).isEqualTo("Number of issues filtered by severity level");
        assertThat(QualityGateMetric.NEW_ISSUES.getDescription()).isEqualTo("Total number of new issues found");
        assertThat(QualityGateMetric.ISSUES_BY_CATEGORY.getDescription()).isEqualTo("Number of issues filtered by category");
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(QualityGateMetric.valueOf("ISSUES_BY_SEVERITY")).isEqualTo(QualityGateMetric.ISSUES_BY_SEVERITY);
        assertThat(QualityGateMetric.valueOf("NEW_ISSUES")).isEqualTo(QualityGateMetric.NEW_ISSUES);
        assertThat(QualityGateMetric.valueOf("ISSUES_BY_CATEGORY")).isEqualTo(QualityGateMetric.ISSUES_BY_CATEGORY);
    }
}

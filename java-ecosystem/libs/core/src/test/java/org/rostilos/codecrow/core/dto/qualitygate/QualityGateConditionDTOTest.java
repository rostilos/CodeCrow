package org.rostilos.codecrow.core.dto.qualitygate;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateComparator;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateCondition;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateMetric;

import java.lang.reflect.Field;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("QualityGateConditionDTO")
class QualityGateConditionDTOTest {

    @Nested
    @DisplayName("setters and getters")
    class SettersAndGettersTests {

        @Test
        @DisplayName("should set and get all fields")
        void shouldSetAndGetAllFields() {
            QualityGateConditionDTO dto = new QualityGateConditionDTO();
            dto.setId(1L);
            dto.setMetric(QualityGateMetric.ISSUES_BY_SEVERITY);
            dto.setSeverity(IssueSeverity.HIGH);
            dto.setComparator(QualityGateComparator.GREATER_THAN);
            dto.setThresholdValue(5);
            dto.setEnabled(true);

            assertThat(dto.getId()).isEqualTo(1L);
            assertThat(dto.getMetric()).isEqualTo(QualityGateMetric.ISSUES_BY_SEVERITY);
            assertThat(dto.getSeverity()).isEqualTo(IssueSeverity.HIGH);
            assertThat(dto.getComparator()).isEqualTo(QualityGateComparator.GREATER_THAN);
            assertThat(dto.getThresholdValue()).isEqualTo(5);
            assertThat(dto.isEnabled()).isTrue();
        }

        @Test
        @DisplayName("should handle disabled condition")
        void shouldHandleDisabledCondition() {
            QualityGateConditionDTO dto = new QualityGateConditionDTO();
            dto.setEnabled(false);

            assertThat(dto.isEnabled()).isFalse();
        }

        @Test
        @DisplayName("should handle all metric types")
        void shouldHandleAllMetricTypes() {
            for (QualityGateMetric metric : QualityGateMetric.values()) {
                QualityGateConditionDTO dto = new QualityGateConditionDTO();
                dto.setMetric(metric);
                assertThat(dto.getMetric()).isEqualTo(metric);
            }
        }

        @Test
        @DisplayName("should handle all severity levels")
        void shouldHandleAllSeverityLevels() {
            for (IssueSeverity severity : IssueSeverity.values()) {
                QualityGateConditionDTO dto = new QualityGateConditionDTO();
                dto.setSeverity(severity);
                assertThat(dto.getSeverity()).isEqualTo(severity);
            }
        }

        @Test
        @DisplayName("should handle all comparators")
        void shouldHandleAllComparators() {
            for (QualityGateComparator comparator : QualityGateComparator.values()) {
                QualityGateConditionDTO dto = new QualityGateConditionDTO();
                dto.setComparator(comparator);
                assertThat(dto.getComparator()).isEqualTo(comparator);
            }
        }

        @Test
        @DisplayName("should handle zero threshold")
        void shouldHandleZeroThreshold() {
            QualityGateConditionDTO dto = new QualityGateConditionDTO();
            dto.setThresholdValue(0);

            assertThat(dto.getThresholdValue()).isZero();
        }

        @Test
        @DisplayName("should handle null severity")
        void shouldHandleNullSeverity() {
            QualityGateConditionDTO dto = new QualityGateConditionDTO();
            dto.setSeverity(null);

            assertThat(dto.getSeverity()).isNull();
        }
    }

    @Nested
    @DisplayName("fromEntity()")
    class FromEntityTests {

        @Test
        @DisplayName("should convert condition with all fields")
        void shouldConvertConditionWithAllFields() {
            QualityGateCondition entity = new QualityGateCondition();
            setField(entity, "id", 1L);
            entity.setMetric(QualityGateMetric.ISSUES_BY_SEVERITY);
            entity.setSeverity(IssueSeverity.HIGH);
            entity.setComparator(QualityGateComparator.GREATER_THAN);
            entity.setThresholdValue(0);
            entity.setEnabled(true);

            QualityGateConditionDTO dto = QualityGateConditionDTO.fromEntity(entity);

            assertThat(dto.getId()).isEqualTo(1L);
            assertThat(dto.getMetric()).isEqualTo(QualityGateMetric.ISSUES_BY_SEVERITY);
            assertThat(dto.getSeverity()).isEqualTo(IssueSeverity.HIGH);
            assertThat(dto.getComparator()).isEqualTo(QualityGateComparator.GREATER_THAN);
            assertThat(dto.getThresholdValue()).isZero();
            assertThat(dto.isEnabled()).isTrue();
        }

        @Test
        @DisplayName("should convert disabled condition")
        void shouldConvertDisabledCondition() {
            QualityGateCondition entity = new QualityGateCondition();
            setField(entity, "id", 2L);
            entity.setMetric(QualityGateMetric.NEW_ISSUES);
            entity.setComparator(QualityGateComparator.LESS_THAN);
            entity.setThresholdValue(10);
            entity.setEnabled(false);

            QualityGateConditionDTO dto = QualityGateConditionDTO.fromEntity(entity);

            assertThat(dto.isEnabled()).isFalse();
        }

        @Test
        @DisplayName("should convert condition with MEDIUM severity")
        void shouldConvertConditionWithMediumSeverity() {
            QualityGateCondition entity = new QualityGateCondition();
            setField(entity, "id", 3L);
            entity.setMetric(QualityGateMetric.ISSUES_BY_SEVERITY);
            entity.setSeverity(IssueSeverity.MEDIUM);
            entity.setComparator(QualityGateComparator.GREATER_THAN_OR_EQUAL);
            entity.setThresholdValue(5);
            entity.setEnabled(true);

            QualityGateConditionDTO dto = QualityGateConditionDTO.fromEntity(entity);

            assertThat(dto.getSeverity()).isEqualTo(IssueSeverity.MEDIUM);
        }

        @Test
        @DisplayName("should convert condition with LOW severity")
        void shouldConvertConditionWithLowSeverity() {
            QualityGateCondition entity = new QualityGateCondition();
            setField(entity, "id", 4L);
            entity.setMetric(QualityGateMetric.ISSUES_BY_SEVERITY);
            entity.setSeverity(IssueSeverity.LOW);
            entity.setComparator(QualityGateComparator.NOT_EQUAL);
            entity.setThresholdValue(100);
            entity.setEnabled(true);

            QualityGateConditionDTO dto = QualityGateConditionDTO.fromEntity(entity);

            assertThat(dto.getSeverity()).isEqualTo(IssueSeverity.LOW);
        }

        @Test
        @DisplayName("should convert condition without severity")
        void shouldConvertConditionWithoutSeverity() {
            QualityGateCondition entity = new QualityGateCondition();
            setField(entity, "id", 5L);
            entity.setMetric(QualityGateMetric.NEW_ISSUES);
            entity.setSeverity(null);
            entity.setComparator(QualityGateComparator.LESS_THAN_OR_EQUAL);
            entity.setThresholdValue(50);
            entity.setEnabled(true);

            QualityGateConditionDTO dto = QualityGateConditionDTO.fromEntity(entity);

            assertThat(dto.getSeverity()).isNull();
            assertThat(dto.getMetric()).isEqualTo(QualityGateMetric.NEW_ISSUES);
        }

        @Test
        @DisplayName("should convert condition with ISSUES_BY_CATEGORY metric")
        void shouldConvertConditionWithCategoryMetric() {
            QualityGateCondition entity = new QualityGateCondition();
            setField(entity, "id", 6L);
            entity.setMetric(QualityGateMetric.ISSUES_BY_CATEGORY);
            entity.setComparator(QualityGateComparator.EQUAL);
            entity.setThresholdValue(0);
            entity.setEnabled(true);

            QualityGateConditionDTO dto = QualityGateConditionDTO.fromEntity(entity);

            assertThat(dto.getMetric()).isEqualTo(QualityGateMetric.ISSUES_BY_CATEGORY);
        }
    }

    private void setField(Object obj, String fieldName, Object value) {
        try {
            Field field = obj.getClass().getDeclaredField(fieldName);
            field.setAccessible(true);
            field.set(obj, value);
        } catch (Exception e) {
            throw new RuntimeException("Failed to set field " + fieldName, e);
        }
    }
}

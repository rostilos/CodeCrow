package org.rostilos.codecrow.core.dto.qualitygate;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.qualitygate.QualityGate;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateComparator;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateCondition;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateMetric;

import java.lang.reflect.Field;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("QualityGateDTO")
class QualityGateDTOTest {

    @Nested
    @DisplayName("setters and getters")
    class SettersAndGettersTests {

        @Test
        @DisplayName("should set and get all fields")
        void shouldSetAndGetAllFields() {
            OffsetDateTime now = OffsetDateTime.now();
            List<QualityGateConditionDTO> conditions = new ArrayList<>();

            QualityGateDTO dto = new QualityGateDTO();
            dto.setId(1L);
            dto.setName("Default Quality Gate");
            dto.setDescription("Standard quality gate for all projects");
            dto.setDefault(true);
            dto.setActive(true);
            dto.setConditions(conditions);
            dto.setCreatedAt(now);
            dto.setUpdatedAt(now);

            assertThat(dto.getId()).isEqualTo(1L);
            assertThat(dto.getName()).isEqualTo("Default Quality Gate");
            assertThat(dto.getDescription()).isEqualTo("Standard quality gate for all projects");
            assertThat(dto.isDefault()).isTrue();
            assertThat(dto.isActive()).isTrue();
            assertThat(dto.getConditions()).isEqualTo(conditions);
            assertThat(dto.getCreatedAt()).isEqualTo(now);
            assertThat(dto.getUpdatedAt()).isEqualTo(now);
        }

        @Test
        @DisplayName("should handle non-default quality gate")
        void shouldHandleNonDefaultQualityGate() {
            QualityGateDTO dto = new QualityGateDTO();
            dto.setDefault(false);
            dto.setActive(true);

            assertThat(dto.isDefault()).isFalse();
            assertThat(dto.isActive()).isTrue();
        }

        @Test
        @DisplayName("should handle inactive quality gate")
        void shouldHandleInactiveQualityGate() {
            QualityGateDTO dto = new QualityGateDTO();
            dto.setActive(false);

            assertThat(dto.isActive()).isFalse();
        }

        @Test
        @DisplayName("should handle empty conditions")
        void shouldHandleEmptyConditions() {
            QualityGateDTO dto = new QualityGateDTO();
            dto.setConditions(Collections.emptyList());

            assertThat(dto.getConditions()).isEmpty();
        }

        @Test
        @DisplayName("should handle conditions list")
        void shouldHandleConditionsList() {
            QualityGateConditionDTO condition1 = new QualityGateConditionDTO();
            condition1.setId(1L);

            QualityGateConditionDTO condition2 = new QualityGateConditionDTO();
            condition2.setId(2L);

            List<QualityGateConditionDTO> conditions = List.of(condition1, condition2);

            QualityGateDTO dto = new QualityGateDTO();
            dto.setConditions(conditions);

            assertThat(dto.getConditions()).hasSize(2);
        }

        @Test
        @DisplayName("should handle null description")
        void shouldHandleNullDescription() {
            QualityGateDTO dto = new QualityGateDTO();
            dto.setDescription(null);

            assertThat(dto.getDescription()).isNull();
        }
    }

    @Nested
    @DisplayName("fromEntity()")
    class FromEntityTests {

        @Test
        @DisplayName("should convert QualityGate with all fields")
        void shouldConvertQualityGateWithAllFields() {
            QualityGate entity = new QualityGate();
            setField(entity, "id", 1L);
            entity.setName("Strict Quality Gate");
            entity.setDescription("High standards for code quality");
            entity.setDefault(true);
            entity.setActive(true);
            entity.setConditions(new ArrayList<>());

            QualityGateDTO dto = QualityGateDTO.fromEntity(entity);

            assertThat(dto.getId()).isEqualTo(1L);
            assertThat(dto.getName()).isEqualTo("Strict Quality Gate");
            assertThat(dto.getDescription()).isEqualTo("High standards for code quality");
            assertThat(dto.isDefault()).isTrue();
            assertThat(dto.isActive()).isTrue();
            assertThat(dto.getConditions()).isEmpty();
        }

        @Test
        @DisplayName("should convert QualityGate with conditions")
        void shouldConvertQualityGateWithConditions() {
            QualityGate entity = new QualityGate();
            setField(entity, "id", 2L);
            entity.setName("Standard Gate");
            entity.setDefault(false);
            entity.setActive(true);

            QualityGateCondition condition1 = new QualityGateCondition();
            setField(condition1, "id", 10L);
            condition1.setMetric(QualityGateMetric.ISSUES_BY_SEVERITY);
            condition1.setSeverity(IssueSeverity.HIGH);
            condition1.setComparator(QualityGateComparator.LESS_THAN);
            condition1.setThresholdValue(5);
            condition1.setEnabled(true);

            QualityGateCondition condition2 = new QualityGateCondition();
            setField(condition2, "id", 11L);
            condition2.setMetric(QualityGateMetric.NEW_ISSUES);
            condition2.setComparator(QualityGateComparator.EQUAL);
            condition2.setThresholdValue(0);
            condition2.setEnabled(true);

            entity.setConditions(List.of(condition1, condition2));

            QualityGateDTO dto = QualityGateDTO.fromEntity(entity);

            assertThat(dto.getConditions()).hasSize(2);
            assertThat(dto.getConditions().get(0).getId()).isEqualTo(10L);
            assertThat(dto.getConditions().get(0).getMetric()).isEqualTo(QualityGateMetric.ISSUES_BY_SEVERITY);
            assertThat(dto.getConditions().get(0).getSeverity()).isEqualTo(IssueSeverity.HIGH);
            assertThat(dto.getConditions().get(1).getId()).isEqualTo(11L);
            assertThat(dto.getConditions().get(1).getMetric()).isEqualTo(QualityGateMetric.NEW_ISSUES);
        }

        @Test
        @DisplayName("should convert inactive QualityGate")
        void shouldConvertInactiveQualityGate() {
            QualityGate entity = new QualityGate();
            setField(entity, "id", 3L);
            entity.setName("Inactive Gate");
            entity.setDefault(false);
            entity.setActive(false);
            entity.setConditions(new ArrayList<>());

            QualityGateDTO dto = QualityGateDTO.fromEntity(entity);

            assertThat(dto.isActive()).isFalse();
            assertThat(dto.isDefault()).isFalse();
        }

        @Test
        @DisplayName("should convert QualityGate with null description")
        void shouldConvertQualityGateWithNullDescription() {
            QualityGate entity = new QualityGate();
            setField(entity, "id", 5L);
            entity.setName("Minimal Gate");
            entity.setDescription(null);
            entity.setDefault(false);
            entity.setActive(true);
            entity.setConditions(new ArrayList<>());

            QualityGateDTO dto = QualityGateDTO.fromEntity(entity);

            assertThat(dto.getDescription()).isNull();
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

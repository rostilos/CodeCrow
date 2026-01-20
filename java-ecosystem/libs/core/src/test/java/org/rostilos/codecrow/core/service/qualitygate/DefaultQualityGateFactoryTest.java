package org.rostilos.codecrow.core.service.qualitygate;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.qualitygate.QualityGate;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateComparator;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateCondition;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateMetric;
import org.rostilos.codecrow.core.model.workspace.Workspace;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("DefaultQualityGateFactory")
class DefaultQualityGateFactoryTest {

    @Nested
    @DisplayName("createStandardGate()")
    class StandardGateTests {

        @Test
        @DisplayName("should create gate with correct name and description")
        void shouldCreateGateWithCorrectNameAndDescription() {
            Workspace workspace = new Workspace();
            
            QualityGate gate = DefaultQualityGateFactory.createStandardGate(workspace);
            
            assertThat(gate.getName()).isEqualTo("CodeCrow Standard");
            assertThat(gate.getDescription()).contains("high severity").contains("medium severity");
        }

        @Test
        @DisplayName("should set gate as default and active")
        void shouldSetGateAsDefaultAndActive() {
            Workspace workspace = new Workspace();
            
            QualityGate gate = DefaultQualityGateFactory.createStandardGate(workspace);
            
            assertThat(gate.isDefault()).isTrue();
            assertThat(gate.isActive()).isTrue();
        }

        @Test
        @DisplayName("should associate gate with workspace")
        void shouldAssociateGateWithWorkspace() {
            Workspace workspace = new Workspace();
            
            QualityGate gate = DefaultQualityGateFactory.createStandardGate(workspace);
            
            assertThat(gate.getWorkspace()).isSameAs(workspace);
        }

        @Test
        @DisplayName("should create two conditions for standard gate")
        void shouldCreateTwoConditions() {
            Workspace workspace = new Workspace();
            
            QualityGate gate = DefaultQualityGateFactory.createStandardGate(workspace);
            
            assertThat(gate.getConditions()).hasSize(2);
        }

        @Test
        @DisplayName("should create HIGH severity condition with threshold 0")
        void shouldCreateHighSeverityCondition() {
            Workspace workspace = new Workspace();
            
            QualityGate gate = DefaultQualityGateFactory.createStandardGate(workspace);
            
            QualityGateCondition highCondition = findConditionBySeverity(gate, IssueSeverity.HIGH);
            
            assertThat(highCondition).isNotNull();
            assertThat(highCondition.getMetric()).isEqualTo(QualityGateMetric.ISSUES_BY_SEVERITY);
            assertThat(highCondition.getComparator()).isEqualTo(QualityGateComparator.GREATER_THAN);
            assertThat(highCondition.getThresholdValue()).isEqualTo(0);
            assertThat(highCondition.isEnabled()).isTrue();
        }

        @Test
        @DisplayName("should create MEDIUM severity condition with threshold 5")
        void shouldCreateMediumSeverityCondition() {
            Workspace workspace = new Workspace();
            
            QualityGate gate = DefaultQualityGateFactory.createStandardGate(workspace);
            
            QualityGateCondition mediumCondition = findConditionBySeverity(gate, IssueSeverity.MEDIUM);
            
            assertThat(mediumCondition).isNotNull();
            assertThat(mediumCondition.getMetric()).isEqualTo(QualityGateMetric.ISSUES_BY_SEVERITY);
            assertThat(mediumCondition.getComparator()).isEqualTo(QualityGateComparator.GREATER_THAN);
            assertThat(mediumCondition.getThresholdValue()).isEqualTo(5);
            assertThat(mediumCondition.isEnabled()).isTrue();
        }
    }

    @Nested
    @DisplayName("createStrictGate()")
    class StrictGateTests {

        @Test
        @DisplayName("should create gate with correct name")
        void shouldCreateGateWithCorrectName() {
            Workspace workspace = new Workspace();
            
            QualityGate gate = DefaultQualityGateFactory.createStrictGate(workspace);
            
            assertThat(gate.getName()).isEqualTo("CodeCrow Strict");
        }

        @Test
        @DisplayName("should not set as default")
        void shouldNotSetAsDefault() {
            Workspace workspace = new Workspace();
            
            QualityGate gate = DefaultQualityGateFactory.createStrictGate(workspace);
            
            assertThat(gate.isDefault()).isFalse();
            assertThat(gate.isActive()).isTrue();
        }

        @Test
        @DisplayName("should create three conditions for strict gate")
        void shouldCreateThreeConditions() {
            Workspace workspace = new Workspace();
            
            QualityGate gate = DefaultQualityGateFactory.createStrictGate(workspace);
            
            assertThat(gate.getConditions()).hasSize(3);
        }

        @Test
        @DisplayName("should fail on any HIGH, MEDIUM, or LOW issues")
        void shouldFailOnAnyIssues() {
            Workspace workspace = new Workspace();
            
            QualityGate gate = DefaultQualityGateFactory.createStrictGate(workspace);
            
            QualityGateCondition highCondition = findConditionBySeverity(gate, IssueSeverity.HIGH);
            QualityGateCondition mediumCondition = findConditionBySeverity(gate, IssueSeverity.MEDIUM);
            QualityGateCondition lowCondition = findConditionBySeverity(gate, IssueSeverity.LOW);
            
            assertThat(highCondition.getThresholdValue()).isEqualTo(0);
            assertThat(mediumCondition.getThresholdValue()).isEqualTo(0);
            assertThat(lowCondition.getThresholdValue()).isEqualTo(0);
            
            assertThat(highCondition.getComparator()).isEqualTo(QualityGateComparator.GREATER_THAN);
            assertThat(mediumCondition.getComparator()).isEqualTo(QualityGateComparator.GREATER_THAN);
            assertThat(lowCondition.getComparator()).isEqualTo(QualityGateComparator.GREATER_THAN);
        }
    }

    @Nested
    @DisplayName("createLenientGate()")
    class LenientGateTests {

        @Test
        @DisplayName("should create gate with correct name")
        void shouldCreateGateWithCorrectName() {
            Workspace workspace = new Workspace();
            
            QualityGate gate = DefaultQualityGateFactory.createLenientGate(workspace);
            
            assertThat(gate.getName()).isEqualTo("CodeCrow Lenient");
        }

        @Test
        @DisplayName("should create only one condition for lenient gate")
        void shouldCreateOnlyOneCondition() {
            Workspace workspace = new Workspace();
            
            QualityGate gate = DefaultQualityGateFactory.createLenientGate(workspace);
            
            assertThat(gate.getConditions()).hasSize(1);
        }

        @Test
        @DisplayName("should only fail on HIGH severity issues")
        void shouldOnlyFailOnHighSeverity() {
            Workspace workspace = new Workspace();
            
            QualityGate gate = DefaultQualityGateFactory.createLenientGate(workspace);
            
            QualityGateCondition condition = gate.getConditions().get(0);
            
            assertThat(condition.getSeverity()).isEqualTo(IssueSeverity.HIGH);
            assertThat(condition.getThresholdValue()).isEqualTo(0);
            assertThat(condition.getComparator()).isEqualTo(QualityGateComparator.GREATER_THAN);
        }
    }

    @Nested
    @DisplayName("Condition-QualityGate relationship")
    class RelationshipTests {

        @Test
        @DisplayName("should set quality gate reference on all conditions")
        void shouldSetQualityGateReferenceOnConditions() {
            Workspace workspace = new Workspace();
            
            QualityGate gate = DefaultQualityGateFactory.createStandardGate(workspace);
            
            for (QualityGateCondition condition : gate.getConditions()) {
                assertThat(condition.getQualityGate()).isSameAs(gate);
            }
        }
    }

    // Helper methods

    private QualityGateCondition findConditionBySeverity(QualityGate gate, IssueSeverity severity) {
        return gate.getConditions().stream()
            .filter(c -> c.getSeverity() == severity)
            .findFirst()
            .orElse(null);
    }
}

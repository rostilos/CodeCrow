package org.rostilos.codecrow.core.model.qualitygate;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.workspace.Workspace;

import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class QualityGateTest {

    @Test
    void testDefaultConstructor() {
        QualityGate gate = new QualityGate();
        assertThat(gate.getId()).isNull();
        assertThat(gate.getWorkspace()).isNull();
        assertThat(gate.getName()).isNull();
        assertThat(gate.getDescription()).isNull();
        assertThat(gate.isDefault()).isFalse();
        assertThat(gate.isActive()).isTrue();
        assertThat(gate.getConditions()).isEmpty();
    }

    @Test
    void testSetAndGetWorkspace() {
        QualityGate gate = new QualityGate();
        Workspace workspace = new Workspace();
        gate.setWorkspace(workspace);
        assertThat(gate.getWorkspace()).isEqualTo(workspace);
    }

    @Test
    void testSetAndGetName() {
        QualityGate gate = new QualityGate();
        gate.setName("Strict Gate");
        assertThat(gate.getName()).isEqualTo("Strict Gate");
    }

    @Test
    void testSetAndGetDescription() {
        QualityGate gate = new QualityGate();
        gate.setDescription("No high or medium issues allowed");
        assertThat(gate.getDescription()).isEqualTo("No high or medium issues allowed");
    }

    @Test
    void testSetAndGetIsDefault() {
        QualityGate gate = new QualityGate();
        gate.setDefault(true);
        assertThat(gate.isDefault()).isTrue();
    }

    @Test
    void testSetAndGetActive() {
        QualityGate gate = new QualityGate();
        gate.setActive(false);
        assertThat(gate.isActive()).isFalse();
    }

    @Test
    void testAddCondition() {
        QualityGate gate = new QualityGate();
        QualityGateCondition condition = new QualityGateCondition();
        condition.setMetric(QualityGateMetric.ISSUES_BY_SEVERITY);
        
        gate.addCondition(condition);
        
        assertThat(gate.getConditions()).hasSize(1);
        assertThat(gate.getConditions().get(0)).isEqualTo(condition);
        assertThat(condition.getQualityGate()).isEqualTo(gate);
    }

    @Test
    void testRemoveCondition() {
        QualityGate gate = new QualityGate();
        QualityGateCondition condition = new QualityGateCondition();
        
        gate.addCondition(condition);
        assertThat(gate.getConditions()).hasSize(1);
        
        gate.removeCondition(condition);
        assertThat(gate.getConditions()).isEmpty();
        assertThat(condition.getQualityGate()).isNull();
    }

    @Test
    void testSetConditions() {
        QualityGate gate = new QualityGate();
        QualityGateCondition condition1 = new QualityGateCondition();
        QualityGateCondition condition2 = new QualityGateCondition();
        List<QualityGateCondition> conditions = new ArrayList<>();
        conditions.add(condition1);
        conditions.add(condition2);
        
        gate.setConditions(conditions);
        
        assertThat(gate.getConditions()).hasSize(2);
        assertThat(condition1.getQualityGate()).isEqualTo(gate);
        assertThat(condition2.getQualityGate()).isEqualTo(gate);
    }

    @Test
    void testOnUpdate() {
        QualityGate gate = new QualityGate();
        gate.onUpdate();
        // Just verify the method doesn't throw an exception
        assertThat(gate).isNotNull();
    }

    @Test
    void testFullQualityGateSetup() {
        QualityGate gate = new QualityGate();
        Workspace workspace = new Workspace();
        
        gate.setWorkspace(workspace);
        gate.setName("Production Gate");
        gate.setDescription("Quality gate for production releases");
        gate.setDefault(true);
        gate.setActive(true);
        
        QualityGateCondition condition = new QualityGateCondition();
        condition.setMetric(QualityGateMetric.NEW_ISSUES);
        gate.addCondition(condition);
        
        assertThat(gate.getWorkspace()).isEqualTo(workspace);
        assertThat(gate.getName()).isEqualTo("Production Gate");
        assertThat(gate.getDescription()).isEqualTo("Quality gate for production releases");
        assertThat(gate.isDefault()).isTrue();
        assertThat(gate.isActive()).isTrue();
        assertThat(gate.getConditions()).hasSize(1);
    }

    @Test
    void testActiveDefaultsToTrue() {
        QualityGate gate = new QualityGate();
        assertThat(gate.isActive()).isTrue();
    }

    @Test
    void testIsDefaultDefaultsToFalse() {
        QualityGate gate = new QualityGate();
        assertThat(gate.isDefault()).isFalse();
    }

    @Test
    void testConditionsListIsInitialized() {
        QualityGate gate = new QualityGate();
        assertThat(gate.getConditions()).isNotNull();
        assertThat(gate.getConditions()).isEmpty();
    }
}

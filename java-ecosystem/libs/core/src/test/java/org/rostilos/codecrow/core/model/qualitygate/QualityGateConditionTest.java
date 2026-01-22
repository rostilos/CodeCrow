package org.rostilos.codecrow.core.model.qualitygate;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;

import static org.assertj.core.api.Assertions.assertThat;

class QualityGateConditionTest {

    @Test
    void testDefaultConstructor() {
        QualityGateCondition condition = new QualityGateCondition();
        assertThat(condition.getId()).isNull();
        assertThat(condition.getQualityGate()).isNull();
        assertThat(condition.getMetric()).isNull();
        assertThat(condition.getSeverity()).isNull();
        assertThat(condition.getComparator()).isNull();
        assertThat(condition.getThresholdValue()).isZero();
        assertThat(condition.isEnabled()).isTrue();
    }

    @Test
    void testSetAndGetQualityGate() {
        QualityGateCondition condition = new QualityGateCondition();
        QualityGate gate = new QualityGate();
        condition.setQualityGate(gate);
        assertThat(condition.getQualityGate()).isEqualTo(gate);
    }

    @Test
    void testSetAndGetMetric() {
        QualityGateCondition condition = new QualityGateCondition();
        condition.setMetric(QualityGateMetric.NEW_ISSUES);
        assertThat(condition.getMetric()).isEqualTo(QualityGateMetric.NEW_ISSUES);
    }

    @Test
    void testSetAndGetSeverity() {
        QualityGateCondition condition = new QualityGateCondition();
        condition.setSeverity(IssueSeverity.HIGH);
        assertThat(condition.getSeverity()).isEqualTo(IssueSeverity.HIGH);
    }

    @Test
    void testSetAndGetComparator() {
        QualityGateCondition condition = new QualityGateCondition();
        condition.setComparator(QualityGateComparator.GREATER_THAN);
        assertThat(condition.getComparator()).isEqualTo(QualityGateComparator.GREATER_THAN);
    }

    @Test
    void testSetAndGetThresholdValue() {
        QualityGateCondition condition = new QualityGateCondition();
        condition.setThresholdValue(5);
        assertThat(condition.getThresholdValue()).isEqualTo(5);
    }

    @Test
    void testSetAndGetEnabled() {
        QualityGateCondition condition = new QualityGateCondition();
        condition.setEnabled(false);
        assertThat(condition.isEnabled()).isFalse();
    }

    @Test
    void testEvaluate_GreaterThan() {
        QualityGateCondition condition = new QualityGateCondition();
        condition.setComparator(QualityGateComparator.GREATER_THAN);
        condition.setThresholdValue(5);
        condition.setEnabled(true);
        
        assertThat(condition.evaluate(6)).isTrue();
        assertThat(condition.evaluate(5)).isFalse();
        assertThat(condition.evaluate(4)).isFalse();
    }

    @Test
    void testEvaluate_GreaterThanOrEqual() {
        QualityGateCondition condition = new QualityGateCondition();
        condition.setComparator(QualityGateComparator.GREATER_THAN_OR_EQUAL);
        condition.setThresholdValue(5);
        condition.setEnabled(true);
        
        assertThat(condition.evaluate(6)).isTrue();
        assertThat(condition.evaluate(5)).isTrue();
        assertThat(condition.evaluate(4)).isFalse();
    }

    @Test
    void testEvaluate_LessThan() {
        QualityGateCondition condition = new QualityGateCondition();
        condition.setComparator(QualityGateComparator.LESS_THAN);
        condition.setThresholdValue(5);
        condition.setEnabled(true);
        
        assertThat(condition.evaluate(4)).isTrue();
        assertThat(condition.evaluate(5)).isFalse();
        assertThat(condition.evaluate(6)).isFalse();
    }

    @Test
    void testEvaluate_LessThanOrEqual() {
        QualityGateCondition condition = new QualityGateCondition();
        condition.setComparator(QualityGateComparator.LESS_THAN_OR_EQUAL);
        condition.setThresholdValue(5);
        condition.setEnabled(true);
        
        assertThat(condition.evaluate(4)).isTrue();
        assertThat(condition.evaluate(5)).isTrue();
        assertThat(condition.evaluate(6)).isFalse();
    }

    @Test
    void testEvaluate_Equal() {
        QualityGateCondition condition = new QualityGateCondition();
        condition.setComparator(QualityGateComparator.EQUAL);
        condition.setThresholdValue(5);
        condition.setEnabled(true);
        
        assertThat(condition.evaluate(5)).isTrue();
        assertThat(condition.evaluate(4)).isFalse();
        assertThat(condition.evaluate(6)).isFalse();
    }

    @Test
    void testEvaluate_NotEqual() {
        QualityGateCondition condition = new QualityGateCondition();
        condition.setComparator(QualityGateComparator.NOT_EQUAL);
        condition.setThresholdValue(5);
        condition.setEnabled(true);
        
        assertThat(condition.evaluate(4)).isTrue();
        assertThat(condition.evaluate(6)).isTrue();
        assertThat(condition.evaluate(5)).isFalse();
    }

    @Test
    void testEvaluate_DisabledConditionAlwaysPasses() {
        QualityGateCondition condition = new QualityGateCondition();
        condition.setComparator(QualityGateComparator.GREATER_THAN);
        condition.setThresholdValue(5);
        condition.setEnabled(false);
        
        assertThat(condition.evaluate(10)).isTrue();
        assertThat(condition.evaluate(0)).isTrue();
    }

    @Test
    void testFails() {
        QualityGateCondition condition = new QualityGateCondition();
        condition.setComparator(QualityGateComparator.GREATER_THAN);
        condition.setThresholdValue(0);
        condition.setEnabled(true);
        
        assertThat(condition.fails(1)).isTrue();  // HIGH > 0 means fail
        assertThat(condition.fails(0)).isFalse();
    }

    @Test
    void testFullConditionSetup() {
        QualityGateCondition condition = new QualityGateCondition();
        QualityGate gate = new QualityGate();
        
        condition.setQualityGate(gate);
        condition.setMetric(QualityGateMetric.ISSUES_BY_SEVERITY);
        condition.setSeverity(IssueSeverity.HIGH);
        condition.setComparator(QualityGateComparator.GREATER_THAN);
        condition.setThresholdValue(0);
        condition.setEnabled(true);
        
        assertThat(condition.getQualityGate()).isEqualTo(gate);
        assertThat(condition.getMetric()).isEqualTo(QualityGateMetric.ISSUES_BY_SEVERITY);
        assertThat(condition.getSeverity()).isEqualTo(IssueSeverity.HIGH);
        assertThat(condition.getComparator()).isEqualTo(QualityGateComparator.GREATER_THAN);
        assertThat(condition.getThresholdValue()).isZero();
        assertThat(condition.isEnabled()).isTrue();
    }

    @Test
    void testEnabledDefaultsToTrue() {
        QualityGateCondition condition = new QualityGateCondition();
        assertThat(condition.isEnabled()).isTrue();
    }
}

package org.rostilos.codecrow.core.service.qualitygate;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisResult;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.qualitygate.QualityGate;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateComparator;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateCondition;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateMetric;
import org.rostilos.codecrow.core.service.qualitygate.QualityGateEvaluator.ConditionResult;
import org.rostilos.codecrow.core.service.qualitygate.QualityGateEvaluator.QualityGateResult;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("QualityGateEvaluator")
class QualityGateEvaluatorTest {

    private QualityGateEvaluator evaluator;

    @BeforeEach
    void setUp() {
        evaluator = new QualityGateEvaluator();
    }

    @Nested
    @DisplayName("evaluate() - basic scenarios")
    class EvaluateBasicTests {

        @Test
        @DisplayName("should return SKIPPED when quality gate is null")
        void shouldReturnSkippedWhenQualityGateIsNull() {
            CodeAnalysis analysis = createAnalysis(0, 0, 0, 0);

            QualityGateResult result = evaluator.evaluate(analysis, null);

            assertThat(result.isSkipped()).isTrue();
            assertThat(result.result()).isEqualTo(AnalysisResult.SKIPPED);
            assertThat(result.qualityGateName()).isNull();
            assertThat(result.conditionResults()).isEmpty();
        }

        @Test
        @DisplayName("should return SKIPPED when quality gate is inactive")
        void shouldReturnSkippedWhenQualityGateIsInactive() {
            CodeAnalysis analysis = createAnalysis(5, 0, 0, 0);
            QualityGate gate = createQualityGate("Test Gate", false);

            QualityGateResult result = evaluator.evaluate(analysis, gate);

            assertThat(result.isSkipped()).isTrue();
        }

        @Test
        @DisplayName("should return PASSED when no conditions configured")
        void shouldReturnPassedWhenNoConditions() {
            CodeAnalysis analysis = createAnalysis(10, 10, 10, 0);
            QualityGate gate = createQualityGate("Empty Gate", true);

            QualityGateResult result = evaluator.evaluate(analysis, gate);

            assertThat(result.isPassed()).isTrue();
            assertThat(result.qualityGateName()).isEqualTo("Empty Gate");
            assertThat(result.conditionResults()).isEmpty();
        }

        @Test
        @DisplayName("should skip disabled conditions")
        void shouldSkipDisabledConditions() {
            CodeAnalysis analysis = createAnalysis(10, 0, 0, 0); // 10 high issues
            QualityGate gate = createQualityGate("Test Gate", true);
            
            QualityGateCondition condition = createCondition(
                QualityGateMetric.ISSUES_BY_SEVERITY,
                IssueSeverity.HIGH,
                QualityGateComparator.GREATER_THAN,
                0
            );
            condition.setEnabled(false); // Disabled
            gate.addCondition(condition);

            QualityGateResult result = evaluator.evaluate(analysis, gate);

            assertThat(result.isPassed()).isTrue();
            assertThat(result.conditionResults()).isEmpty();
        }
    }

    @Nested
    @DisplayName("evaluate() - HIGH severity conditions")
    class HighSeverityTests {

        @Test
        @DisplayName("should PASS when high issues is 0 and threshold is > 0")
        void shouldPassWhenNoHighIssues() {
            CodeAnalysis analysis = createAnalysis(0, 5, 10, 0);
            QualityGate gate = createStandardGate();

            QualityGateResult result = evaluator.evaluate(analysis, gate);

            assertThat(result.isPassed()).isTrue();
        }

        @Test
        @DisplayName("should FAIL when high issues exceed threshold")
        void shouldFailWhenHighIssuesExceedThreshold() {
            CodeAnalysis analysis = createAnalysis(1, 0, 0, 0); // 1 high issue
            QualityGate gate = createStandardGate(); // HIGH > 0 fails

            QualityGateResult result = evaluator.evaluate(analysis, gate);

            assertThat(result.isFailed()).isTrue();
            assertThat(result.getFailedConditions()).hasSize(1);
            assertThat(result.getFailedConditions().get(0).severity()).isEqualTo(IssueSeverity.HIGH);
        }

        @Test
        @DisplayName("should include correct actual value in result")
        void shouldIncludeCorrectActualValue() {
            CodeAnalysis analysis = createAnalysis(5, 0, 0, 0);
            QualityGate gate = createStandardGate();

            QualityGateResult result = evaluator.evaluate(analysis, gate);

            ConditionResult conditionResult = result.conditionResults().stream()
                .filter(c -> c.severity() == IssueSeverity.HIGH)
                .findFirst()
                .orElseThrow();

            assertThat(conditionResult.actualValue()).isEqualTo(5);
            assertThat(conditionResult.threshold()).isEqualTo(0);
            assertThat(conditionResult.passed()).isFalse();
        }
    }

    @Nested
    @DisplayName("evaluate() - MEDIUM severity conditions")
    class MediumSeverityTests {

        @Test
        @DisplayName("should PASS when medium issues within threshold")
        void shouldPassWhenMediumIssuesWithinThreshold() {
            CodeAnalysis analysis = createAnalysis(0, 5, 0, 0); // 5 medium issues
            QualityGate gate = createStandardGate(); // MEDIUM > 5 fails

            QualityGateResult result = evaluator.evaluate(analysis, gate);

            assertThat(result.isPassed()).isTrue();
        }

        @Test
        @DisplayName("should FAIL when medium issues exceed threshold")
        void shouldFailWhenMediumIssuesExceedThreshold() {
            CodeAnalysis analysis = createAnalysis(0, 6, 0, 0); // 6 medium issues
            QualityGate gate = createStandardGate(); // MEDIUM > 5 fails

            QualityGateResult result = evaluator.evaluate(analysis, gate);

            assertThat(result.isFailed()).isTrue();
            assertThat(result.getFailedConditions()).hasSize(1);
            assertThat(result.getFailedConditions().get(0).severity()).isEqualTo(IssueSeverity.MEDIUM);
        }
    }

    @Nested
    @DisplayName("evaluate() - multiple conditions")
    class MultipleConditionsTests {

        @Test
        @DisplayName("should PASS when all conditions pass")
        void shouldPassWhenAllConditionsPass() {
            CodeAnalysis analysis = createAnalysis(0, 3, 10, 0);
            QualityGate gate = createStandardGate();

            QualityGateResult result = evaluator.evaluate(analysis, gate);

            assertThat(result.isPassed()).isTrue();
            assertThat(result.conditionResults()).hasSize(2);
            assertThat(result.conditionResults()).allMatch(ConditionResult::passed);
        }

        @Test
        @DisplayName("should FAIL when any condition fails")
        void shouldFailWhenAnyConditionFails() {
            CodeAnalysis analysis = createAnalysis(1, 10, 0, 0); // Both fail
            QualityGate gate = createStandardGate();

            QualityGateResult result = evaluator.evaluate(analysis, gate);

            assertThat(result.isFailed()).isTrue();
            assertThat(result.getFailedConditions()).hasSize(2);
        }

        @Test
        @DisplayName("should track both passed and failed conditions")
        void shouldTrackBothPassedAndFailedConditions() {
            CodeAnalysis analysis = createAnalysis(0, 10, 0, 0); // Only medium fails
            QualityGate gate = createStandardGate();

            QualityGateResult result = evaluator.evaluate(analysis, gate);

            assertThat(result.isFailed()).isTrue();
            assertThat(result.conditionResults()).hasSize(2);
            assertThat(result.getFailedConditions()).hasSize(1);
            
            long passedCount = result.conditionResults().stream()
                .filter(ConditionResult::passed)
                .count();
            assertThat(passedCount).isEqualTo(1);
        }
    }

    @Nested
    @DisplayName("evaluate() - comparator types")
    class ComparatorTests {

        @Test
        @DisplayName("should handle GREATER_THAN_OR_EQUAL comparator")
        void shouldHandleGreaterThanOrEqual() {
            CodeAnalysis analysis = createAnalysis(5, 0, 0, 0);
            QualityGate gate = createQualityGate("Test", true);
            gate.addCondition(createCondition(
                QualityGateMetric.ISSUES_BY_SEVERITY,
                IssueSeverity.HIGH,
                QualityGateComparator.GREATER_THAN_OR_EQUAL,
                5
            ));

            QualityGateResult result = evaluator.evaluate(analysis, gate);

            assertThat(result.isFailed()).isTrue(); // 5 >= 5 is true, so fails
        }

        @Test
        @DisplayName("should handle LESS_THAN comparator")
        void shouldHandleLessThan() {
            CodeAnalysis analysis = createAnalysis(3, 0, 0, 0);
            QualityGate gate = createQualityGate("Test", true);
            gate.addCondition(createCondition(
                QualityGateMetric.ISSUES_BY_SEVERITY,
                IssueSeverity.HIGH,
                QualityGateComparator.LESS_THAN,
                5
            ));

            QualityGateResult result = evaluator.evaluate(analysis, gate);

            assertThat(result.isFailed()).isTrue(); // 3 < 5 is true, so fails
        }

        @Test
        @DisplayName("should handle EQUAL comparator")
        void shouldHandleEqual() {
            CodeAnalysis analysis = createAnalysis(5, 0, 0, 0);
            QualityGate gate = createQualityGate("Test", true);
            gate.addCondition(createCondition(
                QualityGateMetric.ISSUES_BY_SEVERITY,
                IssueSeverity.HIGH,
                QualityGateComparator.EQUAL,
                5
            ));

            QualityGateResult result = evaluator.evaluate(analysis, gate);

            assertThat(result.isFailed()).isTrue(); // 5 == 5 is true, so fails
        }

        @Test
        @DisplayName("should handle NOT_EQUAL comparator")
        void shouldHandleNotEqual() {
            CodeAnalysis analysis = createAnalysis(3, 0, 0, 0);
            QualityGate gate = createQualityGate("Test", true);
            gate.addCondition(createCondition(
                QualityGateMetric.ISSUES_BY_SEVERITY,
                IssueSeverity.HIGH,
                QualityGateComparator.NOT_EQUAL,
                5
            ));

            QualityGateResult result = evaluator.evaluate(analysis, gate);

            assertThat(result.isFailed()).isTrue(); // 3 != 5 is true, so fails
        }
    }

    @Nested
    @DisplayName("ConditionResult")
    class ConditionResultTests {

        @Test
        @DisplayName("should generate correct description for passed condition")
        void shouldGenerateDescriptionForPassedCondition() {
            ConditionResult result = new ConditionResult(
                QualityGateMetric.ISSUES_BY_SEVERITY,
                IssueSeverity.HIGH,
                ">",
                0,
                0,
                true
            );

            assertThat(result.getDescription()).contains("PASSED");
            assertThat(result.getDescription()).contains("HIGH");
            assertThat(result.getDescription()).contains("> 0");
            assertThat(result.getDescription()).contains("actual: 0");
        }

        @Test
        @DisplayName("should generate correct description for failed condition")
        void shouldGenerateDescriptionForFailedCondition() {
            ConditionResult result = new ConditionResult(
                QualityGateMetric.ISSUES_BY_SEVERITY,
                IssueSeverity.MEDIUM,
                ">",
                5,
                10,
                false
            );

            assertThat(result.getDescription()).contains("FAILED");
            assertThat(result.getDescription()).contains("MEDIUM");
            assertThat(result.getDescription()).contains("> 5");
            assertThat(result.getDescription()).contains("actual: 10");
        }
    }

    @Nested
    @DisplayName("QualityGateResult")
    class QualityGateResultTests {

        @Test
        @DisplayName("skipped() should create correct result")
        void skippedShouldCreateCorrectResult() {
            QualityGateResult result = QualityGateResult.skipped();

            assertThat(result.isSkipped()).isTrue();
            assertThat(result.isPassed()).isFalse();
            assertThat(result.isFailed()).isFalse();
            assertThat(result.qualityGateName()).isNull();
            assertThat(result.conditionResults()).isEmpty();
        }
    }

    // Helper methods

    private CodeAnalysis createAnalysis(int high, int medium, int low, int info) {
        CodeAnalysis analysis = new CodeAnalysis();
        // Use reflection or create a test subclass to set counts
        setIssueCounts(analysis, high, medium, low, info);
        return analysis;
    }

    private void setIssueCounts(CodeAnalysis analysis, int high, int medium, int low, int info) {
        try {
            var highField = CodeAnalysis.class.getDeclaredField("highSeverityCount");
            highField.setAccessible(true);
            highField.setInt(analysis, high);

            var mediumField = CodeAnalysis.class.getDeclaredField("mediumSeverityCount");
            mediumField.setAccessible(true);
            mediumField.setInt(analysis, medium);

            var lowField = CodeAnalysis.class.getDeclaredField("lowSeverityCount");
            lowField.setAccessible(true);
            lowField.setInt(analysis, low);

            var infoField = CodeAnalysis.class.getDeclaredField("infoSeverityCount");
            infoField.setAccessible(true);
            infoField.setInt(analysis, info);

            var totalField = CodeAnalysis.class.getDeclaredField("totalIssues");
            totalField.setAccessible(true);
            totalField.setInt(analysis, high + medium + low + info);
        } catch (Exception e) {
            throw new RuntimeException("Failed to set issue counts", e);
        }
    }

    private QualityGate createQualityGate(String name, boolean active) {
        QualityGate gate = new QualityGate();
        gate.setName(name);
        gate.setActive(active);
        return gate;
    }

    private QualityGateCondition createCondition(
            QualityGateMetric metric,
            IssueSeverity severity,
            QualityGateComparator comparator,
            int threshold) {
        QualityGateCondition condition = new QualityGateCondition();
        condition.setMetric(metric);
        condition.setSeverity(severity);
        condition.setComparator(comparator);
        condition.setThresholdValue(threshold);
        condition.setEnabled(true);
        return condition;
    }

    /**
     * Creates the standard CodeCrow quality gate:
     * - HIGH issues > 0 = FAIL
     * - MEDIUM issues > 5 = FAIL
     */
    private QualityGate createStandardGate() {
        QualityGate gate = createQualityGate("CodeCrow Standard", true);

        gate.addCondition(createCondition(
            QualityGateMetric.ISSUES_BY_SEVERITY,
            IssueSeverity.HIGH,
            QualityGateComparator.GREATER_THAN,
            0
        ));

        gate.addCondition(createCondition(
            QualityGateMetric.ISSUES_BY_SEVERITY,
            IssueSeverity.MEDIUM,
            QualityGateComparator.GREATER_THAN,
            5
        ));

        return gate;
    }
}

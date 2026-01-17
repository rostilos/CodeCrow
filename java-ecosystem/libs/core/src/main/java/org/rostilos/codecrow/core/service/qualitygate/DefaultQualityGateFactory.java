package org.rostilos.codecrow.core.service.qualitygate;

import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.qualitygate.QualityGate;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateComparator;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateCondition;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateMetric;
import org.rostilos.codecrow.core.model.workspace.Workspace;

/**
 * Factory for creating default Quality Gates.
 */
public class DefaultQualityGateFactory {

    /**
     * Creates the default "CodeCrow Standard" quality gate.
     * 
     * Default rules:
     * - HIGH issues > 0 = FAIL
     * - MEDIUM issues > 5 = FAIL
     * - LOW issues - no limit (doesn't affect pass/fail)
     * - INFO issues - no limit (doesn't affect pass/fail)
     */
    public static QualityGate createStandardGate(Workspace workspace) {
        QualityGate gate = new QualityGate();
        gate.setWorkspace(workspace);
        gate.setName("CodeCrow Standard");
        gate.setDescription("Default quality gate: Fails on any high severity issues or more than 5 medium severity issues.");
        gate.setDefault(true);
        gate.setActive(true);

        // HIGH issues > 0 = FAIL
        QualityGateCondition highCondition = new QualityGateCondition();
        highCondition.setMetric(QualityGateMetric.ISSUES_BY_SEVERITY);
        highCondition.setSeverity(IssueSeverity.HIGH);
        highCondition.setComparator(QualityGateComparator.GREATER_THAN);
        highCondition.setThresholdValue(0);
        highCondition.setEnabled(true);
        gate.addCondition(highCondition);

        // MEDIUM issues > 5 = FAIL
        QualityGateCondition mediumCondition = new QualityGateCondition();
        mediumCondition.setMetric(QualityGateMetric.ISSUES_BY_SEVERITY);
        mediumCondition.setSeverity(IssueSeverity.MEDIUM);
        mediumCondition.setComparator(QualityGateComparator.GREATER_THAN);
        mediumCondition.setThresholdValue(5);
        mediumCondition.setEnabled(true);
        gate.addCondition(mediumCondition);

        return gate;
    }

    /**
     * Creates a strict quality gate that fails on any issues.
     */
    public static QualityGate createStrictGate(Workspace workspace) {
        QualityGate gate = new QualityGate();
        gate.setWorkspace(workspace);
        gate.setName("CodeCrow Strict");
        gate.setDescription("Strict quality gate: Fails on any high, medium, or low severity issues.");
        gate.setDefault(false);
        gate.setActive(true);

        // HIGH issues > 0 = FAIL
        QualityGateCondition highCondition = new QualityGateCondition();
        highCondition.setMetric(QualityGateMetric.ISSUES_BY_SEVERITY);
        highCondition.setSeverity(IssueSeverity.HIGH);
        highCondition.setComparator(QualityGateComparator.GREATER_THAN);
        highCondition.setThresholdValue(0);
        highCondition.setEnabled(true);
        gate.addCondition(highCondition);

        // MEDIUM issues > 0 = FAIL
        QualityGateCondition mediumCondition = new QualityGateCondition();
        mediumCondition.setMetric(QualityGateMetric.ISSUES_BY_SEVERITY);
        mediumCondition.setSeverity(IssueSeverity.MEDIUM);
        mediumCondition.setComparator(QualityGateComparator.GREATER_THAN);
        mediumCondition.setThresholdValue(0);
        mediumCondition.setEnabled(true);
        gate.addCondition(mediumCondition);

        // LOW issues > 0 = FAIL
        QualityGateCondition lowCondition = new QualityGateCondition();
        lowCondition.setMetric(QualityGateMetric.ISSUES_BY_SEVERITY);
        lowCondition.setSeverity(IssueSeverity.LOW);
        lowCondition.setComparator(QualityGateComparator.GREATER_THAN);
        lowCondition.setThresholdValue(0);
        lowCondition.setEnabled(true);
        gate.addCondition(lowCondition);

        return gate;
    }

    /**
     * Creates a lenient quality gate that only fails on high severity issues.
     */
    public static QualityGate createLenientGate(Workspace workspace) {
        QualityGate gate = new QualityGate();
        gate.setWorkspace(workspace);
        gate.setName("CodeCrow Lenient");
        gate.setDescription("Lenient quality gate: Only fails on high severity issues.");
        gate.setDefault(false);
        gate.setActive(true);

        // HIGH issues > 0 = FAIL
        QualityGateCondition highCondition = new QualityGateCondition();
        highCondition.setMetric(QualityGateMetric.ISSUES_BY_SEVERITY);
        highCondition.setSeverity(IssueSeverity.HIGH);
        highCondition.setComparator(QualityGateComparator.GREATER_THAN);
        highCondition.setThresholdValue(0);
        highCondition.setEnabled(true);
        gate.addCondition(highCondition);

        return gate;
    }
}

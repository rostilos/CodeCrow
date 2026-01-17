package org.rostilos.codecrow.core.model.qualitygate;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;

/**
 * A single condition within a Quality Gate.
 * 
 * Example conditions:
 * - HIGH issues > 0 = FAIL
 * - MEDIUM issues > 5 = FAIL
 * - LOW issues > 20 = FAIL (optional)
 * - INFO issues > 50 = FAIL (optional)
 */
@Entity
@Table(name = "quality_gate_condition")
public class QualityGateCondition {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "quality_gate_id", nullable = false)
    private QualityGate qualityGate;

    @Column(name = "metric", nullable = false, length = 50)
    @Enumerated(EnumType.STRING)
    private QualityGateMetric metric;

    @Column(name = "severity", length = 20)
    @Enumerated(EnumType.STRING)
    private IssueSeverity severity;

    @Column(name = "comparator", nullable = false, length = 30)
    @Enumerated(EnumType.STRING)
    private QualityGateComparator comparator;

    @Column(name = "threshold_value", nullable = false)
    private int thresholdValue;

    @Column(name = "is_enabled", nullable = false)
    private boolean enabled = true;

    // Getters and Setters
    public Long getId() { return id; }

    public QualityGate getQualityGate() { return qualityGate; }
    public void setQualityGate(QualityGate qualityGate) { this.qualityGate = qualityGate; }

    public QualityGateMetric getMetric() { return metric; }
    public void setMetric(QualityGateMetric metric) { this.metric = metric; }

    public IssueSeverity getSeverity() { return severity; }
    public void setSeverity(IssueSeverity severity) { this.severity = severity; }

    public QualityGateComparator getComparator() { return comparator; }
    public void setComparator(QualityGateComparator comparator) { this.comparator = comparator; }

    public int getThresholdValue() { return thresholdValue; }
    public void setThresholdValue(int thresholdValue) { this.thresholdValue = thresholdValue; }

    public boolean isEnabled() { return enabled; }
    public void setEnabled(boolean enabled) { this.enabled = enabled; }

    /**
     * Evaluate this condition against the given values.
     */
    public boolean evaluate(int actualValue) {
        if (!enabled) return true; // Disabled conditions always pass
        
        return switch (comparator) {
            case GREATER_THAN -> actualValue > thresholdValue;
            case GREATER_THAN_OR_EQUAL -> actualValue >= thresholdValue;
            case LESS_THAN -> actualValue < thresholdValue;
            case LESS_THAN_OR_EQUAL -> actualValue <= thresholdValue;
            case EQUAL -> actualValue == thresholdValue;
            case NOT_EQUAL -> actualValue != thresholdValue;
        };
    }

    /**
     * Check if the condition fails (opposite of passes).
     * A condition FAILS if it evaluates to TRUE (e.g., HIGH > 0 is TRUE means FAIL).
     */
    public boolean fails(int actualValue) {
        return evaluate(actualValue);
    }
}

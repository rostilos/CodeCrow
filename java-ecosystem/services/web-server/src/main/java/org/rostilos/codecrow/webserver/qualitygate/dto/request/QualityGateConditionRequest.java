package org.rostilos.codecrow.webserver.qualitygate.dto.request;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateComparator;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateMetric;

public class QualityGateConditionRequest {

    @NotNull(message = "Metric is required")
    private QualityGateMetric metric;

    private IssueSeverity severity;

    @NotNull(message = "Comparator is required")
    private QualityGateComparator comparator;

    @Min(value = 0, message = "Threshold value must be non-negative")
    private int thresholdValue;

    private boolean enabled = true;

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
}

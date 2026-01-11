package org.rostilos.codecrow.core.dto.qualitygate;

import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateComparator;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateCondition;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateMetric;

public class QualityGateConditionDTO {

    private Long id;
    private QualityGateMetric metric;
    private IssueSeverity severity;
    private QualityGateComparator comparator;
    private int thresholdValue;
    private boolean enabled;

    public static QualityGateConditionDTO fromEntity(QualityGateCondition entity) {
        QualityGateConditionDTO dto = new QualityGateConditionDTO();
        dto.setId(entity.getId());
        dto.setMetric(entity.getMetric());
        dto.setSeverity(entity.getSeverity());
        dto.setComparator(entity.getComparator());
        dto.setThresholdValue(entity.getThresholdValue());
        dto.setEnabled(entity.isEnabled());
        return dto;
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

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

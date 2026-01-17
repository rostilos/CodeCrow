package org.rostilos.codecrow.core.dto.qualitygate;

import org.rostilos.codecrow.core.model.qualitygate.QualityGate;

import java.time.OffsetDateTime;
import java.util.List;

public class QualityGateDTO {

    private Long id;
    private String name;
    private String description;
    private boolean isDefault;
    private boolean active;
    private List<QualityGateConditionDTO> conditions;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;

    public static QualityGateDTO fromEntity(QualityGate entity) {
        QualityGateDTO dto = new QualityGateDTO();
        dto.setId(entity.getId());
        dto.setName(entity.getName());
        dto.setDescription(entity.getDescription());
        dto.setDefault(entity.isDefault());
        dto.setActive(entity.isActive());
        dto.setConditions(entity.getConditions().stream()
                .map(QualityGateConditionDTO::fromEntity)
                .toList());
        dto.setCreatedAt(entity.getCreatedAt());
        dto.setUpdatedAt(entity.getUpdatedAt());
        return dto;
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public String getName() { return name; }
    public void setName(String name) { this.name = name; }

    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }

    public boolean isDefault() { return isDefault; }
    public void setDefault(boolean isDefault) { this.isDefault = isDefault; }

    public boolean isActive() { return active; }
    public void setActive(boolean active) { this.active = active; }

    public List<QualityGateConditionDTO> getConditions() { return conditions; }
    public void setConditions(List<QualityGateConditionDTO> conditions) { this.conditions = conditions; }

    public OffsetDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(OffsetDateTime createdAt) { this.createdAt = createdAt; }

    public OffsetDateTime getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(OffsetDateTime updatedAt) { this.updatedAt = updatedAt; }
}

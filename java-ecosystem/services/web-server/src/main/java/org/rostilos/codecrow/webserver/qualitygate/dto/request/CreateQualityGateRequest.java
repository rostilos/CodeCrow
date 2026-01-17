package org.rostilos.codecrow.webserver.qualitygate.dto.request;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.Size;

import java.util.List;

public class CreateQualityGateRequest {

    @NotBlank(message = "Name is required")
    @Size(max = 100, message = "Name must not exceed 100 characters")
    private String name;

    @Size(max = 500, message = "Description must not exceed 500 characters")
    private String description;

    private boolean isDefault;
    private boolean active = true;

    @NotEmpty(message = "At least one condition is required")
    @Valid
    private List<QualityGateConditionRequest> conditions;

    public String getName() { return name; }
    public void setName(String name) { this.name = name; }

    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }

    public boolean isDefault() { return isDefault; }
    public void setDefault(boolean isDefault) { this.isDefault = isDefault; }

    public boolean isActive() { return active; }
    public void setActive(boolean active) { this.active = active; }

    public List<QualityGateConditionRequest> getConditions() { return conditions; }
    public void setConditions(List<QualityGateConditionRequest> conditions) { this.conditions = conditions; }
}

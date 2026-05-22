package org.rostilos.codecrow.webserver.qualitygate.dto.request;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

import java.util.List;

public class UpdateQualityGateRequest {

    @NotBlank(message = "Name is required")
    @Size(max = 100, message = "Name must not exceed 100 characters")
    private String name;

    @Size(max = 500, message = "Description must not exceed 500 characters")
    private String description;

    private Boolean isDefault;
    private Boolean active;

    @Valid
    private List<QualityGateConditionRequest> conditions;

    public String getName() { return name; }
    public void setName(String name) { this.name = name; }

    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }

    @JsonProperty("isDefault")
    public Boolean isDefault() { return isDefault; }
    @JsonProperty("isDefault")
    public void setDefault(Boolean isDefault) { this.isDefault = isDefault; }

    public Boolean isActive() { return active; }
    public void setActive(Boolean active) { this.active = active; }

    public List<QualityGateConditionRequest> getConditions() { return conditions; }
    public void setConditions(List<QualityGateConditionRequest> conditions) { this.conditions = conditions; }
}

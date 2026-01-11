package org.rostilos.codecrow.core.model.qualitygate;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.workspace.Workspace;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;

/**
 * Quality Gate - a set of rules to determine if analysis passes or fails.
 * Quality Gates can be created at workspace level and applied to projects.
 */
@Entity
@Table(name = "quality_gate", uniqueConstraints = {
        @UniqueConstraint(name = "uq_quality_gate_workspace_name", columnNames = {"workspace_id", "name"})
})
public class QualityGate {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "workspace_id", nullable = false)
    private Workspace workspace;

    @Column(name = "name", nullable = false, length = 100)
    private String name;

    @Column(name = "description", length = 500)
    private String description;

    @Column(name = "is_default", nullable = false)
    private boolean isDefault = false;

    @Column(name = "is_active", nullable = false)
    private boolean active = true;

    @OneToMany(mappedBy = "qualityGate", cascade = CascadeType.ALL, orphanRemoval = true, fetch = FetchType.EAGER)
    private List<QualityGateCondition> conditions = new ArrayList<>();

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    @PreUpdate
    public void onUpdate() {
        this.updatedAt = OffsetDateTime.now();
    }

    public Long getId() { return id; }

    public Workspace getWorkspace() { return workspace; }
    public void setWorkspace(Workspace workspace) { this.workspace = workspace; }

    public String getName() { return name; }
    public void setName(String name) { this.name = name; }

    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }

    public boolean isDefault() { return isDefault; }
    public void setDefault(boolean isDefault) { this.isDefault = isDefault; }

    public boolean isActive() { return active; }
    public void setActive(boolean active) { this.active = active; }

    public List<QualityGateCondition> getConditions() { return conditions; }
    public void setConditions(List<QualityGateCondition> conditions) { 
        this.conditions = conditions;
        conditions.forEach(c -> c.setQualityGate(this));
    }

    public void addCondition(QualityGateCondition condition) {
        conditions.add(condition);
        condition.setQualityGate(this);
    }

    public void removeCondition(QualityGateCondition condition) {
        conditions.remove(condition);
        condition.setQualityGate(null);
    }

    public OffsetDateTime getCreatedAt() { return createdAt; }
    public OffsetDateTime getUpdatedAt() { return updatedAt; }
}

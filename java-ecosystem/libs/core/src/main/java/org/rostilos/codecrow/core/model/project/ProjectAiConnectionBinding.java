package org.rostilos.codecrow.core.model.project;

import com.fasterxml.jackson.annotation.JsonBackReference;
import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.ai.AIConnection;

@Entity
@Table(name = "project_ai_connection")
public class ProjectAiConnectionBinding {

    @Id
    @GeneratedValue
    @Column(nullable = false, updatable = false)
    private Long id;

    @OneToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "project_id", nullable = false)
    @JsonBackReference
    private Project project;

    @ManyToOne(optional = false)
    @JoinColumn(name = "ai_connection_id", nullable = false)
    private AIConnection aiConnection;

    @Column(name = "policy_json", columnDefinition = "TEXT")
    private String policyJson;

    public Long getId() {
        return id;
    }

    public Project getProject() {
        return project;
    }

    public void setProject(Project project) {
        this.project = project;
    }

    public AIConnection getAiConnection() {
        return aiConnection;
    }

    public void setAiConnection(AIConnection aiConnection) {
        this.aiConnection = aiConnection;
    }

    public String getPolicyJson() {
        return policyJson;
    }

    public void setPolicyJson(String policyJson) {
        this.policyJson = policyJson;
    }
}

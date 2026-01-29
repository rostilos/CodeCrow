package org.rostilos.codecrow.core.model.ai;

import java.time.OffsetDateTime;

import com.fasterxml.jackson.annotation.JsonBackReference;
import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.workspace.Workspace;

@Entity
@Table(name = "ai_connection")
public class AIConnection {

    @Id
    @GeneratedValue
    @Column(nullable = false, updatable = false)
    private Long id;
    
    @Column(name = "name", length = 128)
    private String name;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "workspace_id", nullable = false)
    @JsonBackReference
    private Workspace workspace;

    @Enumerated(EnumType.STRING)
    @Column(name = "provider_key", nullable = false, length = 32)
    private AIProviderKey providerKey;

    @Column(name = "ai_model", length = 256)
    private String aiModel;

    @Column(name = "api_key_encrypted", nullable = false)
    private String apiKeyEncrypted;

    @Column(name = "created_at", nullable = false, updatable = false)
    private final OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    @PreUpdate
    public void onUpdate() {
        this.updatedAt = OffsetDateTime.now();
    }

    public Long getId() {
        return id;
    }
    
    public String getName() {
        return name;
    }
    
    public void setName(String name) {
        this.name = name;
    }

    public Workspace getWorkspace() {
        return workspace;
    }

    public void setWorkspace(Workspace workspace) {
        this.workspace = workspace;
    }

    public AIProviderKey getProviderKey() {
        return providerKey;
    }

    public void setProviderKey(AIProviderKey providerKey) {
        this.providerKey = providerKey;
    }

    public String getAiModel() {
        return aiModel;
    }

    public void setAiModel(String aiModel) {
        this.aiModel = aiModel;
    }

    public String getApiKeyEncrypted() {
        return apiKeyEncrypted;
    }

    public void setApiKeyEncrypted(String apiKeyEncrypted) {
        this.apiKeyEncrypted = apiKeyEncrypted;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public OffsetDateTime getUpdatedAt() {
        return updatedAt;
    }
}

package org.rostilos.codecrow.core.model.ai;

import jakarta.persistence.*;
import java.time.OffsetDateTime;

@Entity
@Table(name = "llm_models", indexes = {
    @Index(name = "idx_llm_models_provider", columnList = "provider_key"),
    @Index(name = "idx_llm_models_model_id", columnList = "model_id"),
    @Index(name = "idx_llm_models_search", columnList = "provider_key, model_id")
})
public class LlmModel {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    @Enumerated(EnumType.STRING)
    @Column(name = "provider_key", nullable = false, length = 32)
    private AIProviderKey providerKey;

    @Column(name = "model_id", nullable = false, length = 256)
    private String modelId;

    @Column(name = "display_name", length = 512)
    private String displayName;

    @Column(name = "context_window")
    private Integer contextWindow;

    @Column(name = "supports_tools", nullable = false)
    private boolean supportsTools = false;

    @Column(name = "input_price_per_million", length = 32)
    private String inputPricePerMillion;

    @Column(name = "output_price_per_million", length = 32)
    private String outputPricePerMillion;

    @Column(name = "last_synced_at", nullable = false)
    private OffsetDateTime lastSyncedAt;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt;

    @PrePersist
    public void onCreate() {
        this.createdAt = OffsetDateTime.now();
        if (this.lastSyncedAt == null) {
            this.lastSyncedAt = OffsetDateTime.now();
        }
    }

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public AIProviderKey getProviderKey() {
        return providerKey;
    }

    public void setProviderKey(AIProviderKey providerKey) {
        this.providerKey = providerKey;
    }

    public String getModelId() {
        return modelId;
    }

    public void setModelId(String modelId) {
        this.modelId = modelId;
    }

    public String getDisplayName() {
        return displayName;
    }

    public void setDisplayName(String displayName) {
        this.displayName = displayName;
    }

    public Integer getContextWindow() {
        return contextWindow;
    }

    public void setContextWindow(Integer contextWindow) {
        this.contextWindow = contextWindow;
    }

    public boolean isSupportsTools() {
        return supportsTools;
    }

    public void setSupportsTools(boolean supportsTools) {
        this.supportsTools = supportsTools;
    }

    public OffsetDateTime getLastSyncedAt() {
        return lastSyncedAt;
    }

    public void setLastSyncedAt(OffsetDateTime lastSyncedAt) {
        this.lastSyncedAt = lastSyncedAt;
    }

    public String getInputPricePerMillion() {
        return inputPricePerMillion;
    }

    public void setInputPricePerMillion(String inputPricePerMillion) {
        this.inputPricePerMillion = inputPricePerMillion;
    }

    public String getOutputPricePerMillion() {
        return outputPricePerMillion;
    }

    public void setOutputPricePerMillion(String outputPricePerMillion) {
        this.outputPricePerMillion = outputPricePerMillion;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(OffsetDateTime createdAt) {
        this.createdAt = createdAt;
    }
}

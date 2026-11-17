package org.rostilos.codecrow.webserver.ai.service;

import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.ai.LlmModel;
import org.rostilos.codecrow.core.persistence.repository.ai.LlmModelRepository;
import org.rostilos.codecrow.webserver.ai.dto.response.LlmModelDTO;
import org.rostilos.codecrow.webserver.ai.dto.response.LlmModelListResponse;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.time.OffsetDateTime;
import org.rostilos.codecrow.core.model.ai.LlmModel;

@Service
public class LlmModelService {

    private final LlmModelRepository llmModelRepository;

    public LlmModelService(LlmModelRepository llmModelRepository) {
        this.llmModelRepository = llmModelRepository;
    }

    /**
     * Search LLM models with optional provider filter and search query.
     *
     * @param providerKey Optional provider to filter by
     * @param search      Optional search query (matches modelId and displayName)
     * @param page        Page number (0-indexed)
     * @param size        Page size
     * @return Paginated list of models
     */
    @Transactional(readOnly = true)
    public LlmModelListResponse searchModels(
            AIProviderKey providerKey,
            String search,
            int page,
            int size
    ) {
        Pageable pageable = PageRequest.of(page, size, Sort.by("modelId").ascending());
        Page<LlmModel> modelPage;

        boolean hasSearch = search != null && !search.isBlank();

        if (providerKey != null && hasSearch) {
            modelPage = llmModelRepository.searchByProviderAndQuery(providerKey, search.trim(), pageable);
        } else if (providerKey != null) {
            modelPage = llmModelRepository.findByProviderKey(providerKey, pageable);
        } else if (hasSearch) {
            modelPage = llmModelRepository.searchByQuery(search.trim(), pageable);
        } else {
            modelPage = llmModelRepository.findAll(pageable);
        }

        List<LlmModelDTO> models = modelPage.getContent().stream()
                .map(LlmModelDTO::from)
                .toList();

        return new LlmModelListResponse(
                models,
                page,
                size,
                modelPage.getTotalElements(),
                modelPage.getTotalPages()
        );
    }

    public record ModelProviders(List<LlmModel.ModelProvider> providers, OffsetDateTime syncedAt) {}

    @Transactional(readOnly = true)
    public ModelProviders getOpenRouterProviders(String modelId) {
        return llmModelRepository.findByProviderKeyAndModelId(AIProviderKey.OPENROUTER, modelId)
                .map(model -> new ModelProviders(
                        model.getProviders() == null ? List.of() : model.getProviders(),
                        model.getProvidersSyncedAt()))
                .orElseGet(() -> new ModelProviders(List.of(), null));
    }

    /**
     * Get total count of models for a provider.
     */
    @Transactional(readOnly = true)
    public long countByProvider(AIProviderKey providerKey) {
        return llmModelRepository.countByProviderKey(providerKey);
    }

    /**
     * Check if any models exist in the database.
     */
    @Transactional(readOnly = true)
    public boolean hasModels() {
        return llmModelRepository.count() > 0;
    }

    /**
     * Check if models exist for a specific provider.
     */
    @Transactional(readOnly = true)
    public boolean hasModelsForProvider(AIProviderKey providerKey) {
        return llmModelRepository.existsByProviderKey(providerKey);
    }
}

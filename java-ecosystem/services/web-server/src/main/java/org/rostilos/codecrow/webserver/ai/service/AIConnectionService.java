package org.rostilos.codecrow.webserver.ai.service;

import jakarta.persistence.EntityManager;
import jakarta.persistence.PersistenceContext;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.ai.AiConnectionRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.core.util.SsrfSafeUrlValidator;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.webserver.ai.dto.request.CreateAIConnectionRequest;
import org.rostilos.codecrow.webserver.ai.dto.request.UpdateAiConnectionRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.security.GeneralSecurityException;
import java.util.List;
import java.util.NoSuchElementException;

@Service
public class AIConnectionService {
    @PersistenceContext
    private EntityManager entityManager;

    private final AiConnectionRepository connectionRepository;
    private final TokenEncryptionService tokenEncryptionService;
    private final WorkspaceRepository workspaceRepository;

    public AIConnectionService(
            AiConnectionRepository connectionRepository,
            TokenEncryptionService tokenEncryptionService,
            WorkspaceRepository workspaceRepository
    ) {
        this.connectionRepository = connectionRepository;
        this.tokenEncryptionService = tokenEncryptionService;
        this.workspaceRepository = workspaceRepository;
    }

    @Transactional(readOnly = true)
    public List<AIConnection> listWorkspaceConnections(Long workspaceId) {
        return connectionRepository.findByWorkspace_Id(workspaceId);
    }

    @Transactional
    public AIConnection createAiConnection(Long workspaceId, CreateAIConnectionRequest request) throws GeneralSecurityException {
        Workspace ws = workspaceRepository.findById(workspaceId)
                .orElseThrow(() -> new NoSuchElementException("Workspace not found"));

        // Validate baseUrl for OPENAI_COMPATIBLE provider
        validateBaseUrl(request.providerKey, request.baseUrl);

        AIConnection newAiConnection = new AIConnection();
        String apiKeyEncrypted = tokenEncryptionService.encrypt(request.apiKey);

        newAiConnection.setWorkspace(ws);
        newAiConnection.setName(request.name);
        newAiConnection.setProviderKey(request.providerKey);
        newAiConnection.setAiModel(request.aiModel);
        newAiConnection.setApiKeyEncrypted(apiKeyEncrypted);
        newAiConnection.setBaseUrl(
                request.providerKey == AIProviderKey.OPENAI_COMPATIBLE ? request.baseUrl : null
        );

        return connectionRepository.save(newAiConnection);
    }

    @Transactional
    public AIConnection updateAiConnection(Long workspaceId, Long connectionId, UpdateAiConnectionRequest request) throws GeneralSecurityException {
        AIConnection connection = connectionRepository.findByWorkspace_IdAndId(workspaceId, connectionId)
                .orElseThrow(() -> new NoSuchElementException("Connection not found"));

        if (request.name != null) {
            connection.setName(request.name);
        }
        if (request.providerKey != null) {
            connection.setProviderKey(request.providerKey);
        }
        if(request.aiModel != null && !request.aiModel.isEmpty()) {
            connection.setAiModel(request.aiModel);
        }
        if(request.apiKey != null && !request.apiKey.isEmpty()) {
            String apiKeyEncrypted = tokenEncryptionService.encrypt(request.apiKey);
            connection.setApiKeyEncrypted(apiKeyEncrypted);
        }

        // Handle baseUrl: validate and set for OPENAI_COMPATIBLE, clear for other providers
        AIProviderKey effectiveProvider = request.providerKey != null
                ? request.providerKey : connection.getProviderKey();
        if (effectiveProvider == AIProviderKey.OPENAI_COMPATIBLE) {
            String effectiveBaseUrl = request.baseUrl != null ? request.baseUrl : connection.getBaseUrl();
            validateBaseUrl(effectiveProvider, effectiveBaseUrl);
            if (request.baseUrl != null) {
                connection.setBaseUrl(request.baseUrl);
            }
        } else {
            connection.setBaseUrl(null);
        }

        return connectionRepository.save(connection);
    }


    @Transactional
    public void deleteAiConnection(Long workspaceId, Long connectionId) {
        AIConnection connection = connectionRepository.findByWorkspace_IdAndId(workspaceId, connectionId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));
        connectionRepository.delete(connection);
    }

    /**
     * Validates baseUrl for OPENAI_COMPATIBLE connections.
     * Enforces HTTPS, valid URL format, and rejects private/reserved IPs (SSRF protection).
     */
    private void validateBaseUrl(AIProviderKey providerKey, String baseUrl) {
        if (providerKey == AIProviderKey.OPENAI_COMPATIBLE) {
            if (baseUrl == null || baseUrl.isBlank()) {
                throw new IllegalArgumentException(
                        "Base URL is required for OpenAI Compatible provider");
            }
            // Strip trailing slash for consistency
            String normalized = baseUrl.strip();
            if (normalized.endsWith("/")) {
                normalized = normalized.substring(0, normalized.length() - 1);
            }
            SsrfSafeUrlValidator.validate(normalized);
        }
    }
}

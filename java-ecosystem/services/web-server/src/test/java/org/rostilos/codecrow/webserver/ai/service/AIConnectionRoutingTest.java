package org.rostilos.codecrow.webserver.ai.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.ai.*;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.ai.AiConnectionRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.webserver.ai.dto.request.*;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestTemplate;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.*;
import static org.springframework.test.web.client.response.MockRestResponseCreators.*;

class AIConnectionRoutingTest {
    private final AiConnectionRepository connections = mock(AiConnectionRepository.class);
    private final WorkspaceRepository workspaces = mock(WorkspaceRepository.class);
    private final TokenEncryptionService encryption = mock(TokenEncryptionService.class);
    private final LlmModelService models = mock(LlmModelService.class);
    private final RestTemplate rest = new RestTemplate();
    private final MockRestServiceServer server = MockRestServiceServer.bindTo(rest).build();
    private final ObjectMapper json = new ObjectMapper();
    private final AIConnectionService service = new AIConnectionService(connections, encryption, workspaces, json, rest, models);
    private static final String LOCK = "{\"extra_body\":{\"provider\":{\"order\":[\"azure\"],\"allow_fallbacks\":false}}}";

    @BeforeEach
    void setup() throws Exception {
        when(workspaces.findById(1L)).thenReturn(Optional.of(new Workspace()));
        when(encryption.encrypt(anyString())).thenReturn("encrypted");
        when(encryption.decrypt("encrypted")).thenReturn("test-key");
        when(connections.save(any())).thenAnswer(invocation -> invocation.getArgument(0));
        when(models.getOpenRouterProviders("openai/gpt-4o")).thenReturn(new LlmModelService.ModelProviders(
                List.of(new LlmModel.ModelProvider("azure", "Azure"), new LlmModel.ModelProvider("openai", "OpenAI")),
                OffsetDateTime.now()));
    }

    private CreateAIConnectionRequest request(String routing) {
        var request = new CreateAIConnectionRequest();
        request.providerKey = AIProviderKey.OPENROUTER;
        request.aiModel = "openai/gpt-4o";
        request.apiKey = "test-key";
        request.customParameters = routing;
        return request;
    }

    @Test
    void createsOrderedSelectionAndRejectsProvidersForOtherModels() throws Exception {
        var ordered = LOCK.replace("[\"azure\"]", "[\"azure\",\"openai\"]");
        var connection = service.createAiConnection(1L, request(ordered));
        assertThat(json.readTree(connection.getCustomParameters())).isEqualTo(json.readTree(ordered));
        assertThatThrownBy(() -> service.createAiConnection(1L, request(LOCK.replace("azure", "unknown"))))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> service.createAiConnection(1L, request(LOCK.replace("false", "true"))))
                .isInstanceOf(IllegalArgumentException.class);
        verify(connections, times(1)).save(any());
    }

    @Test
    void testConnectionSendsLockExactlyOnceAndSurfacesFailure() throws Exception {
        var connection = service.createAiConnection(1L, request(LOCK));
        when(connections.findByWorkspace_IdAndId(1L, 7L)).thenReturn(Optional.of(connection));
        server.expect(requestTo("https://openrouter.ai/api/v1/chat/completions"))
                .andExpect(jsonPath("$.provider.order[0]").value("azure"))
                .andExpect(jsonPath("$.provider.allow_fallbacks").value(false))
                .andRespond(withServerError().contentType(MediaType.APPLICATION_JSON).body("{\"error\":\"unavailable\"}"));
        assertThat(service.testAiConnection(1L, 7L).success()).isFalse();
        server.verify();
    }

    @Test
    void preservesUnavailableLockOnUnrelatedEditAndAllowsExplicitClear() throws Exception {
        var connection = service.createAiConnection(1L, request(LOCK));
        when(connections.findByWorkspace_IdAndId(1L, 7L)).thenReturn(Optional.of(connection));
        clearInvocations(models);
        var update = new UpdateAiConnectionRequest();
        update.name = "Renamed";
        update.customParameters = LOCK;
        service.updateAiConnection(1L, 7L, update);
        verifyNoInteractions(models);
        assertThat(json.readTree(connection.getCustomParameters())).isEqualTo(json.readTree(LOCK));
        update.customParameters = "";
        service.updateAiConnection(1L, 7L, update);
        assertThat(connection.getCustomParameters()).isNull();
    }

    @Test
    void changingModelClearsItsOldProviderSelection() throws Exception {
        var connection = service.createAiConnection(1L, request(LOCK));
        when(connections.findByWorkspace_IdAndId(1L, 7L)).thenReturn(Optional.of(connection));
        var update = new UpdateAiConnectionRequest();
        update.aiModel = "different/model";
        service.updateAiConnection(1L, 7L, update);
        assertThat(connection.getCustomParameters()).isNull();
    }

    @Test
    void tenantBoundaryPreventsTestingAnotherWorkspacesConnection() {
        when(connections.findByWorkspace_IdAndId(2L, 7L)).thenReturn(Optional.empty());
        assertThatThrownBy(() -> service.testAiConnection(2L, 7L)).isInstanceOf(java.util.NoSuchElementException.class);
        verifyNoInteractions(encryption);
        server.verify();
    }
}

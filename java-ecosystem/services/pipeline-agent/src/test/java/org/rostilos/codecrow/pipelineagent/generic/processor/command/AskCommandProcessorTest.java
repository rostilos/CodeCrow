package org.rostilos.codecrow.pipelineagent.generic.processor.command;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.aiclient.AiCommandClient;
import org.rostilos.codecrow.analysisengine.aiclient.AiCommandClient.AskRequest;
import org.rostilos.codecrow.analysisengine.aiclient.AiCommandClient.AskResult;
import org.rostilos.codecrow.analysisengine.service.PromptSanitizationService;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectAiConnectionBinding;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookHandler.WebhookResult;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.Map;
import java.util.Optional;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
@DisplayName("AskCommandProcessor")
class AskCommandProcessorTest {

    @Mock private CodeAnalysisService codeAnalysisService;
    @Mock private AiCommandClient aiCommandClient;
    @Mock private TokenEncryptionService tokenEncryptionService;

    private AskCommandProcessor processor;

    @BeforeEach
    void setUp() {
        processor = new AskCommandProcessor(
                codeAnalysisService,
                new PromptSanitizationService(),
                aiCommandClient,
                tokenEncryptionService
        );
    }

    @Test
    @DisplayName("should use fallback response when AI answer is not usable")
    void shouldUseFallbackResponseWhenAiAnswerIsNotUsable() throws Exception {
        assertFallbackResponseWhenAiAnswerIsNotUsable("No output generated");
    }

    @Test
    @DisplayName("should use fallback response when AI answer is literal null")
    void shouldUseFallbackResponseWhenAiAnswerIsLiteralNull() throws Exception {
        assertFallbackResponseWhenAiAnswerIsNotUsable("null");
    }

    private void assertFallbackResponseWhenAiAnswerIsNotUsable(String aiAnswer) throws Exception {
        Project project = createProject();
        WebhookPayload payload = createPayload();

        when(codeAnalysisService.getCodeAnalysisCache(42L, "abc123", 7L)).thenReturn(Optional.empty());
        when(tokenEncryptionService.decrypt("encrypted-ai-key")).thenReturn("ai-key");
        when(tokenEncryptionService.decrypt("encrypted-vcs-token")).thenReturn("vcs-token");
        when(aiCommandClient.ask(
                any(AskRequest.class),
                any()
        )).thenReturn(new AskResult(aiAnswer));

        Consumer<Map<String, Object>> eventConsumer = event -> {};
        WebhookResult result = processor.process(
                payload,
                project,
                eventConsumer,
                Map.of("question", "describe this PR and issues")
        );

        assertThat(result.success()).isTrue();
        assertThat(result.message()).isEqualTo("Answer generated successfully");
        assertThat(result.data().get("content"))
                .asString()
                .contains("I couldn't generate a detailed AI answer for this PR")
                .contains("Run `/codecrow analyze` first")
                .doesNotContain(aiAnswer);
    }

    private Project createProject() {
        Project project = new Project();
        ReflectionTestUtils.setField(project, "id", 42L);
        project.setName("Test Project");
        project.setNamespace("test-project");

        AIConnection aiConnection = new AIConnection();
        aiConnection.setProviderKey(AIProviderKey.OPENAI);
        aiConnection.setAiModel("gpt-4");
        aiConnection.setApiKeyEncrypted("encrypted-ai-key");

        ProjectAiConnectionBinding aiBinding = new ProjectAiConnectionBinding();
        aiBinding.setProject(project);
        aiBinding.setAiConnection(aiConnection);
        project.setAiConnectionBinding(aiBinding);

        VcsConnection vcsConnection = new VcsConnection();
        vcsConnection.setProviderType(EVcsProvider.GITHUB);
        vcsConnection.setConnectionType(EVcsConnectionType.ACCESS_TOKEN);
        vcsConnection.setAccessToken("encrypted-vcs-token");

        VcsRepoBinding vcsBinding = new VcsRepoBinding();
        vcsBinding.setProject(project);
        vcsBinding.setVcsConnection(vcsConnection);
        vcsBinding.setProvider(EVcsProvider.GITHUB);
        vcsBinding.setExternalRepoId("repo-id");
        vcsBinding.setExternalNamespace("codecrow");
        vcsBinding.setExternalRepoSlug("codecrow-public");
        project.setVcsRepoBinding(vcsBinding);

        return project;
    }

    private WebhookPayload createPayload() {
        return new WebhookPayload(
                EVcsProvider.GITHUB,
                "issue_comment",
                "repo-id",
                "codecrow-public",
                "codecrow",
                "7",
                "feature/ask",
                "main",
                "abc123",
                null
        );
    }
}

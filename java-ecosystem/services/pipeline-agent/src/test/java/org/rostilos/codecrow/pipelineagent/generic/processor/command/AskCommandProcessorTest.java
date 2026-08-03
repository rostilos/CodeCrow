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
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.model.VcsPullRequestComment;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
@DisplayName("AskCommandProcessor")
class AskCommandProcessorTest {

    @Mock private CodeAnalysisService codeAnalysisService;
    @Mock private AiCommandClient aiCommandClient;
    @Mock private TokenEncryptionService tokenEncryptionService;
    @Mock private VcsClientProvider vcsClientProvider;
    @Mock private VcsClient vcsClient;

    private AskCommandProcessor processor;

    @BeforeEach
    void setUp() {
        processor = new AskCommandProcessor(
                codeAnalysisService,
                new PromptSanitizationService(),
                aiCommandClient,
                tokenEncryptionService,
                vcsClientProvider
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

    @Test
    @DisplayName("should pass the inline conversation to the AI ask request")
    void shouldPassInlineConversationToAskRequest() throws Exception {
        Project project = createProject();
        WebhookPayload payload = createInlinePayload();
        when(codeAnalysisService.getCodeAnalysisCache(42L, "abc123", 7L))
                .thenReturn(Optional.empty());
        when(tokenEncryptionService.decrypt("encrypted-ai-key")).thenReturn("ai-key");
        when(tokenEncryptionService.decrypt("encrypted-vcs-token")).thenReturn("vcs-token");
        when(vcsClientProvider.getClient(any())).thenReturn(vcsClient);
        when(vcsClient.getPullRequestCommentThread(
                anyString(), anyString(), anyLong(), anyString(), anyString(), anyBoolean()))
                .thenReturn(List.of(
                        new VcsPullRequestComment(
                                "root-1", null, "root-1", "codecrow-bot",
                                "Fractional line numbers are silently truncated\n\n"
                                        + "The conversion uses longValue().\n\n"
                                        + "[codecrow-inline-issue]: #",
                                "2026-08-01T10:00:00Z"),
                        new VcsPullRequestComment(
                                "question-1", "root-1", "root-1", "reviewer",
                                "/codecrow ask explain this issue in more detail",
                                "2026-08-01T10:01:00Z")));
        when(aiCommandClient.ask(any(AskRequest.class), any()))
                .thenReturn(new AskResult("The truncation happens because `longValue()` drops the fraction."));

        WebhookResult result = processor.process(
                payload,
                project,
                event -> {},
                Map.of("question", "explain this issue in more detail"));

        org.mockito.ArgumentCaptor<AskRequest> requestCaptor =
                org.mockito.ArgumentCaptor.forClass(AskRequest.class);
        verify(aiCommandClient).ask(requestCaptor.capture(), any());
        assertThat(requestCaptor.getValue().analysisContext())
                .contains("Review conversation context")
                .contains("Inline discussion location: src/Numbers.java:291")
                .contains("Fractional line numbers are silently truncated")
                .contains("The conversion uses longValue()")
                .doesNotContain("[codecrow-inline-issue]: #")
                .doesNotContain("/codecrow ask explain this issue");
        assertThat(result.data().get("content")).asString()
                .contains("CodeCrow Answer")
                .doesNotContain("<!-- codecrow-ask-response -->");
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

    private WebhookPayload createInlinePayload() {
        WebhookPayload.CommentData comment = new WebhookPayload.CommentData(
                "question-1",
                "/codecrow ask explain this issue in more detail",
                "user-1",
                "reviewer",
                "root-1",
                true,
                "src/Numbers.java",
                291);
        return new WebhookPayload(
                EVcsProvider.GITHUB,
                "pull_request_review_comment",
                "repo-id",
                "codecrow-public",
                "codecrow",
                "7",
                "feature/ask",
                "main",
                "abc123",
                null,
                comment);
    }
}

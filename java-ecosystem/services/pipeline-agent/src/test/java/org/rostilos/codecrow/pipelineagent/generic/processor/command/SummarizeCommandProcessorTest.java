package org.rostilos.codecrow.pipelineagent.generic.processor.command;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.aiclient.AiCommandClient;
import org.rostilos.codecrow.analysisengine.aiclient.AiCommandClient.SummarizeRequest;
import org.rostilos.codecrow.analysisengine.aiclient.AiCommandClient.SummarizeResult;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsReportingService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.codeanalysis.PrSummarizeCache;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectAiConnectionBinding;
import org.rostilos.codecrow.core.model.project.ProjectVcsConnectionBinding;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.PrSummarizeCacheRepository;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookHandler.WebhookResult;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.Map;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
@DisplayName("SummarizeCommandProcessor")
class SummarizeCommandProcessorTest {

    @Mock private VcsServiceFactory vcsServiceFactory;
    @Mock private PrSummarizeCacheRepository summarizeCacheRepository;
    @Mock private AiCommandClient aiCommandClient;
    @Mock private TokenEncryptionService tokenEncryptionService;
    @Mock private VcsReportingService reportingService;

    private SummarizeCommandProcessor processor;

    @BeforeEach
    void setUp() {
        processor = new SummarizeCommandProcessor(
                vcsServiceFactory,
                summarizeCacheRepository,
                aiCommandClient,
                tokenEncryptionService
        );
    }

    @Test
    @DisplayName("should cache fallback content when AI summary is missing")
    void shouldCacheFallbackContentWhenAiSummaryIsMissing() throws Exception {
        Project project = createProject();
        WebhookPayload payload = createPayload();

        when(vcsServiceFactory.getReportingService(EVcsProvider.GITHUB)).thenReturn(reportingService);
        when(tokenEncryptionService.decrypt("encrypted-ai-key")).thenReturn("ai-key");
        when(tokenEncryptionService.decrypt("encrypted-vcs-token")).thenReturn("vcs-token");
        when(aiCommandClient.summarize(
                any(SummarizeRequest.class),
                any()
        )).thenReturn(new SummarizeResult(null, null, "ASCII"));

        ArgumentCaptor<PrSummarizeCache> cacheCaptor = ArgumentCaptor.forClass(PrSummarizeCache.class);
        when(summarizeCacheRepository.save(cacheCaptor.capture())).thenAnswer(invocation -> {
            PrSummarizeCache cache = invocation.getArgument(0);
            ReflectionTestUtils.setField(cache, "id", 101L);
            return cache;
        });

        Consumer<Map<String, Object>> eventConsumer = event -> {};
        WebhookResult result = processor.process(payload, project, eventConsumer);

        assertThat(result.success()).isTrue();
        assertThat(result.message()).isEqualTo("Summary generated successfully");

        PrSummarizeCache savedCache = cacheCaptor.getValue();
        assertThat(savedCache.getSummaryContent()).isNotBlank();
        assertThat(savedCache.getSummaryContent()).contains("could not generate a detailed AI summary");
        assertThat(savedCache.getDiagramType()).isEqualTo(PrSummarizeCache.DiagramType.ASCII);
        assertThat(result.data().get("content"))
                .asString()
                .contains("could not generate a detailed AI summary");
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

        ProjectVcsConnectionBinding vcsBinding = new ProjectVcsConnectionBinding();
        vcsBinding.setProject(project);
        vcsBinding.setVcsConnection(vcsConnection);
        vcsBinding.setWorkspace("codecrow");
        vcsBinding.setRepoSlug("codecrow-public");
        project.setVcsBinding(vcsBinding);

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
                "feature/summarize",
                "main",
                "abc123",
                null
        );
    }
}

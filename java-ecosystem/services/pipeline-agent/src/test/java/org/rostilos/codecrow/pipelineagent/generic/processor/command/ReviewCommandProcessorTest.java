package org.rostilos.codecrow.pipelineagent.generic.processor.command;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.aiclient.AiCommandClient;
import org.rostilos.codecrow.analysisengine.aiclient.AiCommandClient.ReviewRequest;
import org.rostilos.codecrow.analysisengine.aiclient.AiCommandClient.ReviewResult;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectAiConnectionBinding;
import org.rostilos.codecrow.core.model.project.ProjectVcsConnectionBinding;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.springframework.test.util.ReflectionTestUtils;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class ReviewCommandProcessorTest {

    @Test
    void boundsGeneratedReviewAtTheVcsCommentLimit() throws Exception {
        AiCommandClient aiCommandClient = mock(AiCommandClient.class);
        TokenEncryptionService tokenEncryptionService = mock(TokenEncryptionService.class);
        ReviewCommandProcessor processor = new ReviewCommandProcessor(
                aiCommandClient, tokenEncryptionService);
        Project project = createProject();
        String review = "review-evidence-界\n".repeat(5_000) + "TAIL-REVIEW";
        when(tokenEncryptionService.decrypt("encrypted-ai-key")).thenReturn("ai-key");
        when(tokenEncryptionService.decrypt("encrypted-vcs-token")).thenReturn("vcs-token");
        when(aiCommandClient.review(any(ReviewRequest.class), any()))
                .thenReturn(new ReviewResult(review));

        var result = processor.process(createPayload(), project, event -> {});

        assertThat(result.data().get("content")).asString()
                .hasSizeLessThanOrEqualTo(65_050)
                .endsWith("... (truncated)")
                .doesNotContain("TAIL-REVIEW");
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
                "feature/review",
                "main",
                "abc123",
                null
        );
    }
}

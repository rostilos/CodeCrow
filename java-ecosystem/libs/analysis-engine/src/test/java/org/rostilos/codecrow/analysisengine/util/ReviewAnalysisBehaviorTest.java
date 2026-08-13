package org.rostilos.codecrow.analysisengine.util;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.AnalysisScopeConfig;
import org.rostilos.codecrow.core.model.project.config.RagConfig;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class ReviewAnalysisBehaviorTest {

    @Test
    void exposesStableSha256DigestForCurrentBehaviorContract() {
        assertThat(ReviewAnalysisBehavior.DIGEST)
                .isEqualTo("6e2e7eab973ee3df3203ed97c071424b974ee92c7b147c9b9051e64857d4125d")
                .matches("[0-9a-f]{64}");
    }

    @Test
    void requestDigestIsStableForRetriesAndChangesWithReviewConfiguration() {
        AiAnalysisRequest request = mock(AiAnalysisRequest.class);
        when(request.getAiModel()).thenReturn("model-a");
        when(request.getMaxAllowedTokens()).thenReturn(32_000);
        when(request.getUseLocalMcp()).thenReturn(true);

        String first = ReviewAnalysisBehavior.digestFor(request);
        String retry = ReviewAnalysisBehavior.digestFor(request);
        when(request.getAiModel()).thenReturn("model-b");
        String changed = ReviewAnalysisBehavior.digestFor(request);

        assertThat(retry).isEqualTo(first);
        assertThat(changed).isNotEqualTo(first).matches("[0-9a-f]{64}");
    }

    @Test
    void projectDigestChangesWhenScopeOrRagContextContractChanges() {
        Project project = new Project();
        AIConnection connection = new AIConnection();
        connection.setProviderKey(AIProviderKey.OPENAI);
        connection.setAiModel("model-a");
        var config = new org.rostilos.codecrow.core.model.project.config.ProjectConfig();
        project.setConfiguration(config);

        String initial = ReviewAnalysisBehavior.digestFor(project, connection, "github");
        config.setAnalysisScope(new AnalysisScopeConfig(
                java.util.List.of("src/**"), java.util.List.of()));
        String scoped = ReviewAnalysisBehavior.digestFor(project, connection, "github");
        config.setRagConfig(new RagConfig(true, "main", null, null));
        String withRag = ReviewAnalysisBehavior.digestFor(project, connection, "github");

        assertThat(scoped).isNotEqualTo(initial);
        assertThat(withRag).isNotEqualTo(scoped);
    }

    @Test
    void projectAndBuiltRequestProduceTheSameBehaviorDigest() {
        Project project = new Project();
        AIConnection connection = new AIConnection();
        connection.setProviderKey(AIProviderKey.OPENAI);
        connection.setAiModel("model-a");
        connection.setBaseUrl("https://example.test");
        connection.setCustomParameters("{\"temperature\":0}");
        var config = project.getEffectiveConfig();

        AiAnalysisRequest request = mock(AiAnalysisRequest.class);
        when(request.getAiProvider()).thenReturn(connection.getProviderKey());
        when(request.getAiModel()).thenReturn(connection.getAiModel());
        when(request.getAiBaseUrl()).thenReturn(connection.getBaseUrl());
        when(request.getAiCustomParameters()).thenReturn(connection.getCustomParameters());
        when(request.getMaxAllowedTokens()).thenReturn(config.maxAnalysisTokenLimit());
        when(request.getUseLocalMcp()).thenReturn(true);
        when(request.getUseMcpTools()).thenReturn(config.useMcpTools());
        when(request.getRagEnabled()).thenReturn(
                config.ragConfig() != null && config.ragConfig().enabled());
        when(request.getProjectRules()).thenReturn(
                config.getProjectRulesConfig().toEnabledRulesJson());
        when(request.getVcsProvider()).thenReturn("github");
        when(request.getAnalysisBehaviorDigest()).thenReturn(
                ReviewAnalysisBehavior.digestFor(project, connection, "github"));

        assertThat(ReviewAnalysisBehavior.digestFor(request))
                .isEqualTo(ReviewAnalysisBehavior.digestFor(
                        project, connection, "github"));
    }
}

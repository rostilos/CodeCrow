package org.rostilos.codecrow.pipelineagent.execution;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.util.List;
import java.util.Optional;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryIntent;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryTruth;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;

class ReviewDeliveryConfigurationTest {
    private static final String EXECUTION_ID = "execution:delivery-restart";
    private static final String MANIFEST_DIGEST = "1".repeat(64);
    private static final String HEAD_SHA = "2".repeat(40);

    @Test
    void persistedAnalysisTruthRemainsEligibleWithoutTransientRedisState() {
        CodeAnalysisRepository analyses = mock(CodeAnalysisRepository.class);
        CodeAnalysis analysis = analysis();
        ReviewDeliveryIntent intent = intent(ReviewDeliveryTruth.digest(analysis));
        when(analyses.findByExecutionId(EXECUTION_ID))
                .thenReturn(Optional.of(analysis));

        assertThat(ReviewDeliveryConfiguration.isEligible(intent, analyses))
                .isTrue();
    }

    @Test
    void missingOrDivergentPersistedAnalysisTruthFailsClosed() {
        CodeAnalysisRepository analyses = mock(CodeAnalysisRepository.class);
        ReviewDeliveryIntent intent = intent("9".repeat(64));

        assertThat(ReviewDeliveryConfiguration.isEligible(intent, analyses))
                .isFalse();

        when(analyses.findByExecutionId(EXECUTION_ID))
                .thenReturn(Optional.of(analysis()));
        assertThat(ReviewDeliveryConfiguration.isEligible(intent, analyses))
                .isFalse();
    }

    private static CodeAnalysis analysis() {
        CodeAnalysis analysis = mock(CodeAnalysis.class);
        Project project = mock(Project.class);
        when(project.getId()).thenReturn(13L);
        when(analysis.getExecutionId()).thenReturn(EXECUTION_ID);
        when(analysis.getArtifactManifestDigest()).thenReturn(MANIFEST_DIGEST);
        when(analysis.getProject()).thenReturn(project);
        when(analysis.getPrNumber()).thenReturn(42L);
        when(analysis.getCommitHash()).thenReturn(HEAD_SHA);
        when(analysis.getIssues()).thenReturn(List.of());
        return analysis;
    }

    private static ReviewDeliveryIntent intent(String analysisTruthDigest) {
        return new ReviewDeliveryIntent(
                "delivery:restart-proof",
                EXECUTION_ID,
                MANIFEST_DIGEST,
                HEAD_SHA,
                7L,
                "review-output:" + "3".repeat(64),
                "4".repeat(64),
                analysisTruthDigest,
                "github",
                13L,
                42L,
                "ANALYSIS_RESULTS",
                "5".repeat(64));
    }
}

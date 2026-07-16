package org.rostilos.codecrow.pipelineagent.execution;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.io.IOException;
import java.util.List;
import java.util.Optional;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryClaim;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryFailure;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryFailureDisposition;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryIntent;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryOutcome;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryState;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryTruth;
import org.rostilos.codecrow.analysisengine.delivery.ReviewProviderEffectIdentity;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsReportingService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;

class VcsReviewDeliveryGatewayTest {
    private static final String INTENT_ID = "delivery:cr02:summary";
    private static final String EXECUTION_ID = "execution:cr02";
    private static final String MANIFEST_DIGEST = "1".repeat(64);
    private static final String HEAD = "2".repeat(40);
    private static final long HEAD_GENERATION = 3L;
    private static final String REPORT_ARTIFACT_ID =
            "review-output:" + "3".repeat(64);
    private static final String REPORT_DIGEST = "4".repeat(64);
    private static final String PROVIDER = "github";
    private static final String REPOSITORY = "github:octo/codecrow";
    private static final String PUBLICATION_KIND = "ANALYSIS_RESULTS";
    private static final long TENANT_ID = 7L;
    private static final long PROJECT_ID = 13L;
    private static final long PULL_REQUEST_ID = 42L;
    private static final long INTERNAL_PULL_REQUEST_ID = 99L;

    @Test
    void mismatchedDurableEffectIdentityFailsBeforeProviderReporting()
            throws IOException {
        Fixture fixture = fixture("f".repeat(64));

        assertThatThrownBy(() -> fixture.gateway.deliver(fixture.claim))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("provider effect identity");

        verify(fixture.reporting, never()).postAnalysisResults(
                fixture.analysis,
                fixture.project,
                PULL_REQUEST_ID,
                INTERNAL_PULL_REQUEST_ID,
                null);
    }

    @Test
    void successfulProviderReportingAcknowledgesTheDeterministicEffect()
            throws IOException {
        Fixture fixture = fixture(effectIdentity());

        ReviewDeliveryOutcome outcome = fixture.gateway.deliver(fixture.claim);

        assertThat(outcome.state()).isEqualTo(ReviewDeliveryState.DELIVERED);
        assertThat(outcome.providerReceiptId()).isEqualTo(effectIdentity());
        assertThat(outcome.idempotencyKey()).isEqualTo(effectIdentity());
        verify(fixture.reporting).postAnalysisResults(
                fixture.analysis,
                fixture.project,
                PULL_REQUEST_ID,
                INTERNAL_PULL_REQUEST_ID,
                null);
    }

    @Test
    void typedPreEffectFailuresAndUncertainIoHaveDistinctDurableOutcomes()
            throws IOException {
        assertThat(deliverFailure(new ReviewDeliveryFailure(
                ReviewDeliveryFailureDisposition.RETRYABLE,
                "provider_unavailable")).state())
                .isEqualTo(ReviewDeliveryState.RETRYABLE_FAILED);
        assertThat(deliverFailure(new ReviewDeliveryFailure(
                ReviewDeliveryFailureDisposition.PERMANENT,
                "provider_rejected")).state())
                .isEqualTo(ReviewDeliveryState.PERMANENT_FAILED);
        assertThat(deliverFailure(new IOException(
                "connection closed after request body")).state())
                .isEqualTo(ReviewDeliveryState.AMBIGUOUS);
    }

    private static ReviewDeliveryOutcome deliverFailure(IOException failure)
            throws IOException {
        Fixture fixture = fixture(effectIdentity());
        doThrow(failure).when(fixture.reporting).postAnalysisResults(
                fixture.analysis,
                fixture.project,
                PULL_REQUEST_ID,
                INTERNAL_PULL_REQUEST_ID,
                null);
        return fixture.gateway.deliver(fixture.claim);
    }

    private static Fixture fixture(String durableEffectIdentity) {
        CodeAnalysisRepository analyses = mock(CodeAnalysisRepository.class);
        ProjectRepository projects = mock(ProjectRepository.class);
        PullRequestRepository pullRequests = mock(PullRequestRepository.class);
        VcsServiceFactory vcsServices = mock(VcsServiceFactory.class);
        VcsReportingService reporting = mock(VcsReportingService.class);
        CodeAnalysis analysis = mock(CodeAnalysis.class);
        Project project = mock(Project.class);
        PullRequest pullRequest = mock(PullRequest.class);
        Workspace workspace = mock(Workspace.class);
        VcsRepoInfo repository = mock(VcsRepoInfo.class);

        when(project.getId()).thenReturn(PROJECT_ID);
        when(project.getWorkspace()).thenReturn(workspace);
        when(workspace.getId()).thenReturn(TENANT_ID);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repository);
        when(repository.getRepoWorkspace()).thenReturn("octo");
        when(repository.getRepoSlug()).thenReturn("codecrow");

        when(analysis.hasExecutionIdentity()).thenReturn(true);
        when(analysis.getExecutionId()).thenReturn(EXECUTION_ID);
        when(analysis.getArtifactManifestDigest()).thenReturn(MANIFEST_DIGEST);
        when(analysis.getProject()).thenReturn(project);
        when(analysis.getPrNumber()).thenReturn(PULL_REQUEST_ID);
        when(analysis.getCommitHash()).thenReturn(HEAD);
        when(analysis.getIssues()).thenReturn(List.of());
        String truthDigest = ReviewDeliveryTruth.digest(analysis);

        ReviewDeliveryIntent intent = new ReviewDeliveryIntent(
                INTENT_ID,
                EXECUTION_ID,
                MANIFEST_DIGEST,
                HEAD,
                HEAD_GENERATION,
                REPORT_ARTIFACT_ID,
                REPORT_DIGEST,
                truthDigest,
                PROVIDER,
                PROJECT_ID,
                PULL_REQUEST_ID,
                PUBLICATION_KIND,
                durableEffectIdentity);
        ReviewDeliveryClaim claim = new ReviewDeliveryClaim(
                intent, 1, "lease-cr02-1");

        when(analyses.findByExecutionId(EXECUTION_ID))
                .thenReturn(Optional.of(analysis));
        when(projects.findByIdWithFullDetails(PROJECT_ID))
                .thenReturn(Optional.of(project));
        when(pullRequests.findByPrNumberAndProject_id(
                PULL_REQUEST_ID, PROJECT_ID))
                .thenReturn(Optional.of(pullRequest));
        when(pullRequest.getId()).thenReturn(INTERNAL_PULL_REQUEST_ID);
        when(vcsServices.getReportingService(EVcsProvider.GITHUB))
                .thenReturn(reporting);

        return new Fixture(
                new VcsReviewDeliveryGateway(
                        analyses, projects, pullRequests, vcsServices),
                claim,
                reporting,
                analysis,
                project);
    }

    private static String effectIdentity() {
        return ReviewProviderEffectIdentity.derive(
                TENANT_ID,
                PROVIDER,
                REPOSITORY,
                PULL_REQUEST_ID,
                HEAD,
                REPORT_DIGEST,
                PUBLICATION_KIND);
    }

    private record Fixture(
            VcsReviewDeliveryGateway gateway,
            ReviewDeliveryClaim claim,
            VcsReportingService reporting,
            CodeAnalysis analysis,
            Project project) {
    }
}

package org.rostilos.codecrow.pipelineagent.execution;

import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryClaim;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryFailure;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryGateway;
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
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;

import java.io.IOException;
import java.util.Objects;

/** Reloads frozen analysis truth and performs one provider delivery attempt. */
public final class VcsReviewDeliveryGateway implements ReviewDeliveryGateway {
    private final CodeAnalysisRepository analyses;
    private final ProjectRepository projects;
    private final PullRequestRepository pullRequests;
    private final VcsServiceFactory vcsServices;

    public VcsReviewDeliveryGateway(
            CodeAnalysisRepository analyses,
            ProjectRepository projects,
            PullRequestRepository pullRequests,
            VcsServiceFactory vcsServices) {
        this.analyses = Objects.requireNonNull(analyses, "analyses");
        this.projects = Objects.requireNonNull(projects, "projects");
        this.pullRequests = Objects.requireNonNull(pullRequests, "pullRequests");
        this.vcsServices = Objects.requireNonNull(vcsServices, "vcsServices");
    }

    @Override
    public ReviewDeliveryOutcome deliver(ReviewDeliveryClaim claim) {
        ReviewDeliveryIntent intent = claim.intent();
        CodeAnalysis analysis = analyses.findByExecutionId(intent.executionId())
                .orElseThrow(() -> new IllegalStateException(
                        "delivery analysis is missing for " + intent.executionId()));
        requireAnalysisBinding(intent, analysis);
        Project project = projects.findByIdWithFullDetails(intent.projectId())
                .orElseThrow(() -> new IllegalStateException(
                        "delivery project is missing"));
        requireProviderEffectIdentity(intent, project);
        PullRequest pullRequest = pullRequests.findByPrNumberAndProject_id(
                        intent.pullRequestId(), intent.projectId())
                .orElseThrow(() -> new IllegalStateException(
                        "delivery pull request is missing"));
        VcsReportingService reporting = vcsServices.getReportingService(
                EVcsProvider.fromId(intent.provider()));
        try {
            reporting.postAnalysisResults(
                    analysis,
                    project,
                    intent.pullRequestId(),
                    pullRequest.getId(),
                    null);
            return outcome(
                    claim,
                    ReviewDeliveryState.DELIVERED,
                    null,
                    intent.idempotencyKey());
        } catch (ReviewDeliveryFailure failure) {
            ReviewDeliveryState state = switch (failure.disposition()) {
                case RETRYABLE -> ReviewDeliveryState.RETRYABLE_FAILED;
                case PERMANENT -> ReviewDeliveryState.PERMANENT_FAILED;
                case AMBIGUOUS -> ReviewDeliveryState.AMBIGUOUS;
            };
            return outcome(
                    claim,
                    state,
                    failure.reasonCode(),
                    null);
        } catch (IOException uncertainFailure) {
            return outcome(
                    claim,
                    ReviewDeliveryState.AMBIGUOUS,
                    "provider_acknowledgement_unknown",
                    null);
        }
    }

    private static void requireProviderEffectIdentity(
            ReviewDeliveryIntent intent, Project project) {
        if (project.getWorkspace() == null
                || project.getWorkspace().getId() == null
                || project.getWorkspace().getId() <= 0) {
            throw new IllegalStateException(
                    "delivery project tenant identity is missing");
        }
        VcsRepoInfo repository = project.getEffectiveVcsRepoInfo();
        if (repository == null) {
            throw new IllegalStateException(
                    "delivery project repository identity is missing");
        }
        String canonicalRepository = intent.provider()
                + ":"
                + requireRepositoryPart(
                        repository.getRepoWorkspace(), "workspace")
                + "/"
                + requireRepositoryPart(repository.getRepoSlug(), "slug");
        String expected = ReviewProviderEffectIdentity.derive(
                project.getWorkspace().getId(),
                intent.provider(),
                canonicalRepository,
                intent.pullRequestId(),
                intent.snapshotRevision(),
                intent.reportDigest(),
                intent.publicationKind());
        if (!expected.equals(intent.idempotencyKey())) {
            throw new IllegalStateException(
                    "delivery provider effect identity conflicts with durable intent");
        }
    }

    private static String requireRepositoryPart(String value, String field) {
        if (value == null
                || value.isBlank()
                || value.length() > 512
                || value.indexOf('\0') >= 0) {
            throw new IllegalStateException(
                    "delivery repository " + field + " is invalid");
        }
        return value;
    }

    private static void requireAnalysisBinding(
            ReviewDeliveryIntent intent, CodeAnalysis analysis) {
        boolean exact = analysis.hasExecutionIdentity()
                && intent.executionId().equals(analysis.getExecutionId())
                && intent.artifactManifestDigest().equals(
                        analysis.getArtifactManifestDigest())
                && intent.projectId() == analysis.getProject().getId()
                && intent.pullRequestId() == analysis.getPrNumber()
                && intent.snapshotRevision().equalsIgnoreCase(
                        analysis.getCommitHash())
                && intent.analysisTruthDigest().equals(
                        ReviewDeliveryTruth.digest(analysis));
        if (!exact) {
            throw new IllegalStateException(
                    "delivery intent conflicts with persisted analysis truth");
        }
    }

    private static ReviewDeliveryOutcome outcome(
            ReviewDeliveryClaim claim,
            ReviewDeliveryState state,
            String reasonCode,
            String providerReceiptId) {
        return new ReviewDeliveryOutcome(
                state,
                claim.intent().intentId(),
                claim.intent().idempotencyKey(),
                claim.attemptNumber(),
                reasonCode,
                providerReceiptId);
    }
}

package org.rostilos.codecrow.pipelineagent.execution;

import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryGateway;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryIntent;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryOutboxPort;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryService;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryTruth;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.time.Clock;
import java.util.Optional;
import java.util.function.Predicate;

/** Durable, idempotent review delivery for the manifest-bound PR path. */
@Configuration(proxyBeanMethods = false)
public class ReviewDeliveryConfiguration {

    @Bean
    public ReviewDeliveryGateway reviewDeliveryGateway(
            CodeAnalysisRepository analyses,
            ProjectRepository projects,
            PullRequestRepository pullRequests,
            VcsServiceFactory vcsServices) {
        return new VcsReviewDeliveryGateway(
                analyses, projects, pullRequests, vcsServices);
    }

    @Bean
    public ReviewDeliveryService reviewDeliveryService(
            ReviewDeliveryOutboxPort outbox,
            ReviewDeliveryGateway gateway,
            CodeAnalysisRepository analyses) {
        Predicate<ReviewDeliveryIntent> eligible = intent ->
                isEligible(intent, analyses);
        return new ReviewDeliveryService(outbox, gateway, eligible);
    }

    @Bean
    public ReviewDeliveryOutboxWorker reviewDeliveryOutboxWorker(
            ReviewDeliveryOutboxPort outbox,
            ReviewDeliveryService delivery,
            @Value("${codecrow.review.delivery.batch-size:25}") int batchSize) {
        return new ReviewDeliveryOutboxWorker(
                outbox, delivery, Clock.systemUTC(), batchSize);
    }

    static boolean isEligible(
            ReviewDeliveryIntent intent,
            CodeAnalysisRepository analyses) {
        try {
            Optional<CodeAnalysis> analysis = analyses.findByExecutionId(
                    intent.executionId());
            return analysis.isPresent()
                    && intent.analysisTruthDigest().equals(
                            ReviewDeliveryTruth.digest(analysis.orElseThrow()));
        } catch (RuntimeException missingOrDivergentState) {
            return false;
        }
    }
}

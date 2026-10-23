package org.rostilos.codecrow.pipelineagent;

import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.codeanalysis.*;
import org.rostilos.codecrow.core.model.job.*;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.*;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.*;
import org.rostilos.codecrow.core.persistence.repository.job.*;
import org.rostilos.codecrow.core.service.*;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.pipelineagent.qadoc.QaDocQueueService;
import org.rostilos.codecrow.testsupport.base.IntegrationTest;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.SpringBootConfiguration;
import org.springframework.boot.autoconfigure.EnableAutoConfiguration;
import org.springframework.boot.autoconfigure.domain.EntityScan;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import java.time.OffsetDateTime;
import java.util.*;
import java.util.concurrent.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

@IntegrationTest
@SpringBootTest(classes=QaDocRecoveryIT.Config.class,
        properties={"spring.flyway.enabled=false", "spring.jpa.hibernate.ddl-auto=create-drop"})
class QaDocRecoveryIT {
    @SpringBootConfiguration @EnableAutoConfiguration @EntityScan("org.rostilos.codecrow.core.model")
    @EnableJpaRepositories("org.rostilos.codecrow.core.persistence.repository")
    @Import({QaDocQueueService.class, JobService.class, AnalysisLockService.class, ProjectFeaturePolicy.class})
    static class Config {}
    @Autowired EntityManager em;
    @Autowired PlatformTransactionManager transactions;
    @Autowired QaDocQueueService queue;
    @Autowired JobService jobs;
    @Autowired JobRepository repository;
    @Autowired JobLogRepository logs;
    @Autowired CodeAnalysisRepository analyses;
    @Autowired AnalysisLockService locks;

    private CodeAnalysis review() {
        return new TransactionTemplate(transactions).execute(status -> {
            String key=UUID.randomUUID().toString(); var workspace=new Workspace(key,key,"QA recovery"); em.persist(workspace);
            var project=new Project(); project.setWorkspace(workspace); project.setNamespace(key);
            var config=new ProjectConfig(); config.setQaAutoDoc(new QaAutoDocConfig(true,QaAutoDocConfig.TemplateMode.BASE,null,null,null));
            config.setTaskManagement(new TaskManagementConfig(1L,null,null)); project.setConfiguration(config); em.persist(project);
            var service=new CodeAnalysisService(analyses, mock(CodeAnalysisIssueRepository.class),
                    mock(org.rostilos.codecrow.core.persistence.repository.qualitygate.QualityGateRepository.class),
                    mock(org.rostilos.codecrow.core.service.qualitygate.QualityGateEvaluator.class),new IssueDeduplicationService(),
                    new PrChangeSummaryService(analyses,new com.fasterxml.jackson.databind.ObjectMapper()));
            return service.createAnalysisFromAiResponse(project,Map.of("comment","reviewed","issues",List.of()),7L,"main","feature/TASK-1","abc123",null,null);
        });
    }
    @Test void lostCompletionEventIsRecoverableAndEnqueueIsAtomicTenantScopedAndIdempotent() throws Exception {
        var analysis=review(); Long projectId=analysis.getProject().getId();
        assertThat(analyses.findById(analysis.getId()).orElseThrow().isQaDocumentationPending()).isTrue();
        assertThat(queue.enqueue(projectId+10000,analysis.getId())).isEmpty();
        assertThatThrownBy(() -> new TransactionTemplate(transactions).execute(status -> {
            assertThat(queue.enqueue(projectId,analysis.getId())).isPresent(); throw new IllegalStateException("crash before commit");
        })).isInstanceOf(IllegalStateException.class);
        assertThat(analyses.findById(analysis.getId()).orElseThrow().isQaDocumentationPending()).isTrue();
        var executor=Executors.newFixedThreadPool(2);
        try {
            var one=executor.submit(() -> queue.enqueue(projectId,analysis.getId()));
            var two=executor.submit(() -> queue.enqueue(projectId,analysis.getId()));
            var results=List.of(one.get(15,TimeUnit.SECONDS),two.get(15,TimeUnit.SECONDS));
            assertThat(results.stream().filter(Optional::isPresent).count()).isEqualTo(1);
            var job=results.stream().flatMap(Optional::stream).findFirst().orElseThrow();
            assertThat(job.getStatus()).isEqualTo(JobStatus.QUEUED);
            assertThat(job.getInputAnalysisId()).isEqualTo(analysis.getId());
            assertThat(jobs.findWebhookDispatchPayload(job.getId())).isPresent();
            assertThat(analyses.findById(analysis.getId()).orElseThrow().isQaDocumentationPending()).isFalse();
            analysis.setComment("late tracking update"); analyses.saveAndFlush(analysis);
            assertThat(analyses.findById(analysis.getId()).orElseThrow().isQaDocumentationPending()).isFalse();
            jobs.completeJob(claim(job)); repository.deleteById(job.getId());
            assertThat(queue.enqueue(projectId,analysis.getId())).isEmpty();
        } finally { executor.shutdownNow(); }
    }
    private Job claim(Job job) { assertThat(jobs.claimQaJobForExecution(job)).isTrue(); return job; }
    @Test void queuedAutomaticQaWaitsForSourceReviewFinalizationWithoutSpendingAnAttempt() {
        var analysis = review();
        var job = queue.enqueue(analysis.getProject().getId(), analysis.getId()).orElseThrow();
        var queuedAt = repository.findById(job.getId()).orElseThrow().getUpdatedAt();
        // Keep the row ineligible to other cached contexts' background schedulers.
        var recoveryTime = OffsetDateTime.now().plusSeconds(35);
        String key = locks.acquireLock(analysis.getProject(), analysis.getSourceBranchName(),
                AnalysisLockType.PR_ANALYSIS).orElseThrow();
        try {
            assertThat(queue.prepareRetry(job.getId(), recoveryTime)).isFalse();
            assertThat(repository.findById(job.getId()).orElseThrow().getStatus()).isEqualTo(JobStatus.QUEUED);
            assertThat(repository.findById(job.getId()).orElseThrow().getUpdatedAt()).as("deferral must not touch job activity").isEqualTo(queuedAt);
            assertThat(logs.findByJobIdAndStep(job.getId(), "qa_attempt")).isEmpty();
        } finally { locks.releaseLock(key); }
        assertThat(locks.isLocked(analysis.getProject().getId(), analysis.getSourceBranchName(), AnalysisLockType.PR_ANALYSIS))
                .as("source review lease released").isFalse();
        assertThat(queue.prepareRetry(job.getId(), recoveryTime)).isTrue();
    }
    @Test void restartRecoveryHonorsLiveLeaseAndStaleCompletionCannotWinThenRetriesStopExplicitly() {
        var analysis=review(); var job=claim(queue.enqueue(analysis.getProject().getId(),analysis.getId()).orElseThrow());
        var old=repository.findById(job.getId()).orElseThrow();
        var recoveryTime=OffsetDateTime.now().plusMinutes(31);
        String key=locks.acquireLock(analysis.getProject(),"qa-pr:7",AnalysisLockType.QA_DOCUMENTATION).orElseThrow();
        assertThat(locks.extendLock(key,60)).isTrue();
        try { assertThat(queue.prepareRetry(job.getId(),recoveryTime)).isFalse(); }
        finally { locks.releaseLock(key); }
        assertThat(queue.prepareRetry(job.getId(),recoveryTime)).isTrue();
        var current=claim(repository.findById(job.getId()).orElseThrow());
        jobs.completeJob(old); jobs.failJob(old,"old worker failure"); jobs.skipJob(old,"old skip");
        assertThat(repository.findById(job.getId()).orElseThrow().getStatus()).isEqualTo(JobStatus.RUNNING);
        jobs.failJob(current,"task handoff unavailable");
        assertThat(queue.prepareRetry(job.getId(),recoveryTime)).isTrue();
        current=claim(repository.findById(job.getId()).orElseThrow());
        jobs.failJob(current,"task handoff still unavailable");
        assertThat(queue.prepareRetry(job.getId(),recoveryTime)).isFalse();
        assertThat(repository.findById(job.getId()).orElseThrow().getStatus()).isEqualTo(JobStatus.FAILED);
        assertThat(logs.findByJobIdAndStep(job.getId(),"qa_attempt")).hasSize(3);
        assertThat(jobs.claimQaJobForExecution(current)).isFalse();
    }
    @Test void duplicateDispatchClaimsOnlyOnce() throws Exception {
        var analysis=review(); var job=queue.enqueue(analysis.getProject().getId(),analysis.getId()).orElseThrow();
        var copy=repository.findById(job.getId()).orElseThrow();
        var executor=Executors.newFixedThreadPool(2);
        try {
            var a=executor.submit(() -> jobs.claimQaJobForExecution(job)); var b=executor.submit(() -> jobs.claimQaJobForExecution(copy));
            assertThat(List.of(a.get(10,TimeUnit.SECONDS),b.get(10,TimeUnit.SECONDS))).containsExactlyInAnyOrder(true,false);
        } finally { executor.shutdownNow(); }
        assertThat(logs.findByJobIdAndStep(job.getId(),"qa_attempt")).hasSize(1);
    }
}

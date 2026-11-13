package org.rostilos.codecrow.analysisengine;

import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.service.*;
import org.rostilos.codecrow.analysisengine.service.branch.*;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.analysis.AnalysisLockRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.*;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;
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
import java.util.UUID;
import java.util.concurrent.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

@IntegrationTest
@SpringBootTest(classes=BranchFullReconciliationLeaseIT.Config.class, properties={
        "spring.flyway.enabled=false", "spring.jpa.hibernate.ddl-auto=create-drop",
        "analysis.lock.timeout.minutes=1", "analysis.lock.heartbeat.interval.seconds=1"})
class BranchFullReconciliationLeaseIT {
    @SpringBootConfiguration @EnableAutoConfiguration
    @EntityScan("org.rostilos.codecrow.core.model")
    @EnableJpaRepositories(basePackageClasses={AnalysisLockRepository.class, BranchRepository.class, PullRequestRepository.class})
    @Import(AnalysisLockService.class)
    static class Config {}

    @Autowired EntityManager em;
    @Autowired PlatformTransactionManager transactions;
    @Autowired AnalysisLockService locks;
    @Autowired AnalysisLockRepository lockRows;
    @Autowired BranchRepository branches;
    @Autowired BranchIssueRepository issues;
    @Autowired PullRequestRepository prs;

    @Test void longFullReconciliationRenewsItsDatabaseLease() throws Exception { exercise(false); }
    @Test void replacedLeasePreventsImportAndKeepsNewOwnersLock() throws Exception { exercise(true); }

    private void exercise(boolean replaceOwner) throws Exception {
        var tx = new TransactionTemplate(transactions);
        Project project = tx.execute(status -> {
            String name = UUID.randomUUID().toString();
            var workspace = new Workspace(name, name, "lease test"); em.persist(workspace);
            var owner = new Project(); owner.setWorkspace(workspace); owner.setNamespace(name); em.persist(owner);
            var branch = new Branch(); branch.setProject(owner); branch.setBranchName("main");
            branch.setCommitHash("head"); branch.setLastSuccessfulCommitHash("head"); em.persist(branch);
            return owner;
        });
        var validation = mock(ProjectValidationService.class);
        when(validation.getProjectWithConnections(project.getId())).thenReturn(project);
        var statusSync = mock(PullRequestStatusSyncService.class);
        var files = mock(BranchFileOperationsService.class);
        var mapping = mock(BranchIssueMappingService.class);
        var reconciliation = mock(BranchIssueReconciliationService.class);
        when(reconciliation.reanalyzeCandidateIssues(any(), any(), any(), any(), any(), any(), any(), any(), anyBoolean(), any()))
                .thenAnswer(inv -> {
                    ((java.util.function.Consumer<Runnable>) inv.getArgument(9)).accept(() -> {});
                    return new BranchIssueReconciliationService.ReconciliationResult(true, null);
                });
        var entered = new CountDownLatch(1);
        var resume = new CountDownLatch(1);
        when(statusSync.syncOpenPullRequestStates(any(), any())).thenAnswer(invocation -> {
            entered.countDown();
            if (!resume.await(15, TimeUnit.SECONDS)) throw new IllegalStateException("Test did not release provider operation");
            return PullRequestStatusSyncService.SyncResult.empty();
        });
        var service = new BranchFullReconciliationService(validation, branches, issues, prs, locks,
                files, mapping, reconciliation, statusSync);
        ExecutorService executor = Executors.newSingleThreadExecutor();
        Future<?> work = executor.submit(() -> service.fullReconcile(project.getId(), "main", ignored -> {}));
        String replacement = null;
        try {
            assertThat(entered.await(10, TimeUnit.SECONDS)).isTrue();
            var original = lockRows.findByProjectIdAndBranchNameAndAnalysisType(
                    project.getId(), "main", AnalysisLockType.BRANCH_ANALYSIS).orElseThrow();
            if (replaceOwner) {
                locks.releaseLock(original.getLockKey());
                replacement = locks.acquireLock(project, "main", AnalysisLockType.BRANCH_ANALYSIS).orElseThrow();
                resume.countDown();
                assertThatThrownBy(() -> work.get(10, TimeUnit.SECONDS)).hasRootCauseInstanceOf(java.io.IOException.class);
                assertThat(lockRows.findByLockKey(replacement)).isPresent();
                verifyNoInteractions(files, mapping, reconciliation);
            } else {
                OffsetDateTime shortenedExpiry = OffsetDateTime.now().plusSeconds(3);
                tx.executeWithoutResult(status -> {
                    var row = lockRows.findByLockKey(original.getLockKey()).orElseThrow();
                    row.setExpiresAt(shortenedExpiry); lockRows.saveAndFlush(row);
                });
                long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5);
                boolean renewed = false;
                while (System.nanoTime() < deadline) {
                    if (lockRows.findByLockKey(original.getLockKey()).orElseThrow().getExpiresAt().isAfter(shortenedExpiry.plusSeconds(10))) {
                        renewed = true; break;
                    }
                    Thread.sleep(50);
                }
                assertThat(renewed).as("A quiet full reconciliation renews the real PostgreSQL lease").isTrue();
                assertThat(locks.acquireLock(project, "main", AnalysisLockType.BRANCH_ANALYSIS)).isEmpty();
                resume.countDown(); work.get(10, TimeUnit.SECONDS);
                assertThat(lockRows.findByLockKey(original.getLockKey())).isEmpty();
            }
        } finally {
            resume.countDown();
            try { work.get(10, TimeUnit.SECONDS); } catch (ExecutionException expectedWhenLeaseLost) { }
            executor.shutdownNow();
            if (replacement != null) locks.releaseLock(replacement);
        }
    }
}

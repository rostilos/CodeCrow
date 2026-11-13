package org.rostilos.codecrow.webserver.project.service;

import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.service.RepositoryIndexJobQueueService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

/** Immediate durable intake after project/config persistence; the scheduler is recovery. */
@Service
public class RepositoryIndexBootstrapService {
    private static final Logger log = LoggerFactory.getLogger(
            RepositoryIndexBootstrapService.class);

    private final RepositoryIndexJobQueueService queueService;

    public RepositoryIndexBootstrapService(
            RepositoryIndexJobQueueService queueService) {
        this.queueService = queueService;
    }

    public void enqueueAfterCommit(Project project) {
        String branch = eligiblePrimaryBranch(project);
        if (branch == null) {
            return;
        }
        Runnable intake = () -> enqueue(project, branch);
        if (TransactionSynchronizationManager.isSynchronizationActive()
                && TransactionSynchronizationManager.isActualTransactionActive()) {
            TransactionSynchronizationManager.registerSynchronization(
                    new TransactionSynchronization() {
                        @Override
                        public void afterCommit() {
                            intake.run();
                        }
                    });
        } else {
            intake.run();
        }
    }

    private void enqueue(Project project, String branch) {
        try {
            queueService.enqueue(
                    project,
                    branch,
                    null,
                    JobTriggerSource.SCHEDULED);
        } catch (RuntimeException unavailable) {
            // Periodic reconciliation owns recovery if immediate intake fails.
            log.info(
                    "Immediate repository-index intake deferred to reconciliation: "
                            + "project={}, branch={}, detail={}",
                    project != null ? project.getId() : null,
                    branch,
                    unavailable.getMessage());
        }
    }

    private static String eligiblePrimaryBranch(Project project) {
        if (project == null || project.getId() == null
                || project.getConfiguration() == null
                || project.getConfiguration().ragConfig() == null
                || !project.getConfiguration().ragConfig().enabled()) {
            return null;
        }
        String branch = project.getConfiguration().ragConfig().branch();
        if (branch == null || branch.isBlank()) {
            // defaultBranch() deliberately exposes only a persisted main/legacy
            // value. mainBranch() synthesizes "main" when neither exists and
            // would mask a provider-reported default such as "master".
            branch = project.getConfiguration().defaultBranch();
        }
        if ((branch == null || branch.isBlank())
                && project.getVcsRepoBinding() != null) {
            branch = project.getVcsRepoBinding().getDefaultBranch();
        }
        return branch == null || branch.isBlank() ? null : branch.trim();
    }
}

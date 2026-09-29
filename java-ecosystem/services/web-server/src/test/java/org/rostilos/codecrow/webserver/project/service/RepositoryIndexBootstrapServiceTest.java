package org.rostilos.codecrow.webserver.project.service;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.RagConfig;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.service.RepositoryIndexJobQueueService;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.List;

import static org.mockito.Mockito.*;

class RepositoryIndexBootstrapServiceTest {

    @Test
    void intakeRunsImmediatelyWithoutAnOuterTransaction() {
        RepositoryIndexJobQueueService queue = mock(
                RepositoryIndexJobQueueService.class);
        RepositoryIndexBootstrapService service =
                new RepositoryIndexBootstrapService(queue);
        Project project = enabledProject();

        service.enqueueAfterCommit(project);

        verify(queue).enqueue(
                project, "main", null, JobTriggerSource.SCHEDULED);
    }

    @Test
    void transactionalOnboardingQueuesOnlyAfterItsProjectCommit() {
        RepositoryIndexJobQueueService queue = mock(
                RepositoryIndexJobQueueService.class);
        RepositoryIndexBootstrapService service =
                new RepositoryIndexBootstrapService(queue);
        Project project = enabledProject();
        TransactionSynchronizationManager.initSynchronization();
        TransactionSynchronizationManager.setActualTransactionActive(true);
        try {
            service.enqueueAfterCommit(project);
            verifyNoInteractions(queue);

            List<TransactionSynchronization> synchronizations =
                    TransactionSynchronizationManager.getSynchronizations();
            synchronizations.forEach(TransactionSynchronization::afterCommit);

            verify(queue).enqueue(
                    project, "main", null, JobTriggerSource.SCHEDULED);
        } finally {
            TransactionSynchronizationManager.clearSynchronization();
            TransactionSynchronizationManager.setActualTransactionActive(false);
        }
    }

    @Test
    void legacyProjectUsesProviderDefaultInsteadOfSyntheticMain() {
        RepositoryIndexJobQueueService queue = mock(
                RepositoryIndexJobQueueService.class);
        RepositoryIndexBootstrapService service =
                new RepositoryIndexBootstrapService(queue);
        Project project = mock(Project.class);
        ProjectConfig config = new ProjectConfig();
        config.setRagConfig(new RagConfig(true, null));
        VcsRepoBinding binding = mock(VcsRepoBinding.class);
        when(project.getId()).thenReturn(42L);
        when(project.getConfiguration()).thenReturn(config);
        when(project.getVcsRepoBinding()).thenReturn(binding);
        when(binding.getDefaultBranch()).thenReturn("master");

        service.enqueueAfterCommit(project);

        verify(queue).enqueue(
                project, "master", null, JobTriggerSource.SCHEDULED);
    }

    private static Project enabledProject() {
        Project project = mock(Project.class);
        ProjectConfig config = new ProjectConfig();
        config.setMainBranch("main");
        config.setRagConfig(new RagConfig(true, "main"));
        when(project.getId()).thenReturn(42L);
        when(project.getConfiguration()).thenReturn(config);
        return project;
    }
}

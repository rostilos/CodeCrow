package org.rostilos.codecrow.ragengine.branch;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.RagConfig;
import org.rostilos.codecrow.core.service.RepositoryIndexJobQueueService;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.*;

class BranchIndexMaintenanceServiceTest {

    @Test
    void omittedBranchDurablyQueuesPrimaryWithoutExecutingInline() {
        RagOperationsService ragOperations = mock(RagOperationsService.class);
        RepositoryIndexJobQueueService queue = mock(
                RepositoryIndexJobQueueService.class);
        BranchIndexMaintenanceService service = new BranchIndexMaintenanceService(
                ragOperations, queue);
        Project project = enabledProject();
        Job job = mock(Job.class);
        when(job.getExternalId()).thenReturn("job-91");
        when(ragOperations.getBaseBranch(project)).thenReturn("main");
        when(queue.enqueue(project, "main", null, JobTriggerSource.UI))
                .thenReturn(job);
        List<Map<String, Object>> events = new ArrayList<>();

        Map<String, Object> outcome = service.rebuild(project, null, events::add);

        assertThat(outcome.get("status")).isEqualTo("queued");
        assertThat(outcome.get("branches")).isEqualTo(List.of("main"));
        assertThat(outcome.get("failedBranches")).isEqualTo(Map.of());
        assertThat(events).singleElement().satisfies(event -> {
            assertThat(event.get("stage")).isEqualTo("branch_queued");
            assertThat(event.get("jobId")).isEqualTo("job-91");
        });
        verify(queue).enqueue(project, "main", null, JobTriggerSource.UI);
    }

    @Test
    void requestedObservedBranchUsesAnalysisPatternEligibility() {
        RagOperationsService ragOperations = mock(RagOperationsService.class);
        RepositoryIndexJobQueueService queue = mock(
                RepositoryIndexJobQueueService.class);
        BranchIndexMaintenanceService service = new BranchIndexMaintenanceService(
                ragOperations, queue);
        Project project = enabledProject();
        Job job = mock(Job.class);
        when(job.getExternalId()).thenReturn("job-92");
        when(ragOperations.getBaseBranch(project)).thenReturn("main");
        when(ragOperations.shouldHaveBranchIndex(project, "release/preview"))
                .thenReturn(true);
        when(queue.enqueue(
                project, "release/preview", null, JobTriggerSource.UI))
                .thenReturn(job);

        Map<String, Object> outcome = service.rebuild(
                project, " release/preview ", ignored -> { });

        assertThat(outcome.get("branches"))
                .isEqualTo(List.of("release/preview"));
        verify(queue).enqueue(
                project, "release/preview", null, JobTriggerSource.UI);
    }

    @Test
    void branchOutsideAnalysisPatternsIsRejectedBeforeQueueing() {
        RagOperationsService ragOperations = mock(RagOperationsService.class);
        RepositoryIndexJobQueueService queue = mock(
                RepositoryIndexJobQueueService.class);
        BranchIndexMaintenanceService service = new BranchIndexMaintenanceService(
                ragOperations, queue);
        Project project = enabledProject();
        when(ragOperations.getBaseBranch(project)).thenReturn("main");

        assertThatThrownBy(() -> service.rebuild(
                project, "private/experiment", ignored -> { }))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("analysis target/push patterns");
        verifyNoInteractions(queue);
    }

    private static Project enabledProject() {
        Project project = new Project();
        project.setConfiguration(new ProjectConfig(
                false, "main", null, new RagConfig(true, "main")));
        return project;
    }
}

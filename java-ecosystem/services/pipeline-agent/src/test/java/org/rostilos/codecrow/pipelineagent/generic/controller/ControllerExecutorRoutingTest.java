package org.rostilos.codecrow.pipelineagent.generic.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.core.dto.project.ProjectDTO;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.pipelineagent.generic.processor.PipelineActionProcessor;
import org.rostilos.codecrow.pipelineagent.generic.service.PipelineJobService;
import org.rostilos.codecrow.ragengine.branch.BranchIndexMaintenanceService;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;

import java.io.ByteArrayOutputStream;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.Executor;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

class ControllerExecutorRoutingTest {
    @Test
    void manualPrAndBranchProcessingUseAssignedActionExecutor() throws Exception {
        var processor = mock(PipelineActionProcessor.class);
        var jobs = mock(PipelineJobService.class);
        var principal = mock(ProjectDTO.class);
        when(principal.id()).thenReturn(7L);
        when(jobs.createDualConsumer(isNull(), any())).thenAnswer(call -> call.getArgument(1));
        when(processor.processPipelineActionWithConsumer(any(), any(), isNull()))
                .thenReturn(Map.of("status", "complete"));
        AtomicInteger submissions = new AtomicInteger();
        Executor executor = task -> { submissions.incrementAndGet(); task.run(); };
        var controller = new ProviderPipelineActionController(processor, jobs, new ObjectMapper(), true, executor);
        var pr = new PrProcessRequest();
        pr.projectId = 7L;
        pr.pullRequestId = 42L;
        pr.analysisType = AnalysisType.PR_REVIEW;
        pr.targetBranchName = "main";
        pr.sourceBranchName = "feature";
        pr.commitHash = "exact-head";
        var branch = new BranchProcessRequest();
        branch.projectId = 7L;
        branch.analysisType = AnalysisType.BRANCH_ANALYSIS;
        branch.targetBranchName = "main";
        branch.commitHash = "exact-head";

        var prResponse = controller.handlePrWebhook(principal, pr);
        var branchResponse = controller.handleBranchWebhook(principal, branch);

        assertThat(submissions).hasValue(2);
        verify(processor).processPipelineActionWithConsumer(eq(pr), any(), isNull());
        verify(processor).processPipelineActionWithConsumer(eq(branch), any(), isNull());
        for (var response : java.util.List.of(prResponse, branchResponse)) {
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            ((StreamingResponseBody) response.getBody()).writeTo(output);
            assertThat(output.toString(java.nio.charset.StandardCharsets.UTF_8)).contains("\"type\":\"final\"");
        }
    }

    @Test
    void manualIndexingUsesAssignedMaintenanceExecutor() throws Exception {
        var maintenance = mock(BranchIndexMaintenanceService.class);
        var projects = mock(ProjectRepository.class);
        var project = mock(Project.class);
        var principal = mock(ProjectDTO.class);
        when(principal.id()).thenReturn(7L);
        when(principal.namespace()).thenReturn("project");
        when(projects.findByIdWithFullDetails(7L)).thenReturn(Optional.of(project));
        when(maintenance.rebuild(eq(project), eq("main"), any())).thenReturn(Map.of("status", "ready"));
        AtomicInteger submissions = new AtomicInteger();
        Executor executor = task -> { submissions.incrementAndGet(); task.run(); };
        var controller = new RagIndexingController(maintenance, projects, new ObjectMapper(), executor);

        var response = controller.triggerIndexing(principal, new RagIndexingController.RagIndexRequest("main"));
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        response.getBody().writeTo(output);

        assertThat(submissions).hasValue(1);
        verify(maintenance).rebuild(eq(project), eq("main"), any());
        assertThat(output.toString(java.nio.charset.StandardCharsets.UTF_8)).contains("\"status\":\"ready\"");
    }
}

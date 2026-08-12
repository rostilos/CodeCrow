package org.rostilos.codecrow.pipelineagent.generic.processor;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InOrder;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.processor.analysis.BranchAnalysisProcessor;
import org.rostilos.codecrow.analysisengine.processor.analysis.PullRequestAnalysisProcessor;
import org.rostilos.codecrow.analysisengine.service.ProjectValidationService;
import org.rostilos.codecrow.analysisengine.service.branch.BranchAnalysisGateService;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobType;
import org.rostilos.codecrow.core.model.project.Project;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class PipelineActionProcessorDependencyGateTest {

    @Mock private ProjectValidationService projectService;
    @Mock private PullRequestAnalysisProcessor pullRequestAnalysisProcessor;
    @Mock private BranchAnalysisProcessor branchAnalysisProcessor;
    @Mock private BranchAnalysisGateService branchAnalysisGateService;
    @Mock private Project project;

    private PipelineActionProcessor processor;

    @BeforeEach
    void setUp() throws Exception {
        processor = new PipelineActionProcessor(
                projectService,
                pullRequestAnalysisProcessor,
                branchAnalysisProcessor,
                branchAnalysisGateService);
        when(projectService.getProjectWithConnections(1L)).thenReturn(project);
        when(project.getId()).thenReturn(1L);
    }

    @Test
    void pipelinePrResolvesDurableDependenciesBeforeAnalysis() throws Exception {
        PrProcessRequest request = new PrProcessRequest();
        request.projectId = 1L;
        request.analysisType = AnalysisType.PR_REVIEW;
        Job job = new Job();
        job.setJobType(JobType.PR_ANALYSIS);

        when(branchAnalysisGateService.awaitDependencies(
                org.mockito.ArgumentMatchers.eq(1L),
                org.mockito.ArgumentMatchers.eq(job),
                any())).thenReturn(BranchAnalysisGateService.GateResult.READY);
        when(pullRequestAnalysisProcessor.process(any(), any(), any()))
                .thenReturn(Map.of("status", "accepted"));

        Map<String, Object> result = processor.processPipelineActionWithConsumer(
                request, event -> { }, job);

        assertThat(result).containsEntry("status", "accepted");
        InOrder ordered = inOrder(branchAnalysisGateService, pullRequestAnalysisProcessor);
        ordered.verify(branchAnalysisGateService).awaitDependencies(
                org.mockito.ArgumentMatchers.eq(1L),
                org.mockito.ArgumentMatchers.eq(job),
                any());
        ordered.verify(pullRequestAnalysisProcessor).process(
                org.mockito.ArgumentMatchers.eq(request), any(),
                org.mockito.ArgumentMatchers.eq(project));
    }

    @Test
    void pipelineBranchSkipsTheProcessorFallbackAfterDurableGate() throws Exception {
        BranchProcessRequest request = new BranchProcessRequest();
        request.projectId = 1L;
        request.analysisType = AnalysisType.BRANCH_ANALYSIS;
        Job job = new Job();
        job.setJobType(JobType.BRANCH_ANALYSIS);

        when(branchAnalysisGateService.awaitDependencies(
                org.mockito.ArgumentMatchers.eq(1L),
                org.mockito.ArgumentMatchers.eq(job),
                any())).thenReturn(BranchAnalysisGateService.GateResult.READY);
        when(branchAnalysisProcessor.processAfterDependencyGate(any(), any()))
                .thenReturn(Map.of("status", "accepted"));

        Map<String, Object> result = processor.processPipelineActionWithConsumer(
                request, event -> { }, job);

        assertThat(result).containsEntry("status", "accepted");
        verify(branchAnalysisProcessor).processAfterDependencyGate(
                org.mockito.ArgumentMatchers.eq(request), any());
        verify(branchAnalysisProcessor, never()).process(any(), any());
    }
}

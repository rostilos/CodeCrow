package org.rostilos.codecrow.analysisengine.service.vcs;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiRequestPreviousIssueDTO;
import org.rostilos.codecrow.analysisengine.dto.request.processor.AnalysisProcessRequest;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class VcsAiClientServiceDefaultMethodsTest {

    @Test
    void compatibilityOverloadsDelegateToTheLegacyBuilder() throws Exception {
        VcsAiClientService service = mock(
                VcsAiClientService.class,
                org.mockito.Answers.CALLS_REAL_METHODS);
        Project project = mock(Project.class);
        AnalysisProcessRequest request = mock(AnalysisProcessRequest.class);
        AiAnalysisRequest built = mock(AiAnalysisRequest.class);
        List<AiAnalysisRequest> expected = List.of(built);
        doReturn(expected).when(service).buildAiAnalysisRequests(
                any(Project.class),
                any(AnalysisProcessRequest.class),
                any(Optional.class));

        assertThat(service.buildAiAnalysisRequests(
                project, request, Optional.empty(), List.of()))
                .isSameAs(expected);
        assertThat(service.buildAiAnalysisRequestsForBranchReconciliation(
                project, request, List.<AiRequestPreviousIssueDTO>of()))
                .isSameAs(expected);
        assertThat(service.buildAiAnalysisRequestsForBranchReconciliation(
                project, request, List.of(), Map.of()))
                .isSameAs(expected);
        assertThat(service.buildAiAnalysisRequestsForBranchReconciliation(
                project, request, List.of(), Map.of(), "diff"))
                .isSameAs(expected);
        assertThat(service.buildDirectPushAnalysisRequests(
                project, request, "diff", Map.of(), List.of("A.java")))
                .isSameAs(expected);
    }

    @Test
    void exactBuilderFailsClosedWithTheProviderIdentity() {
        VcsAiClientService service = mock(
                VcsAiClientService.class,
                org.mockito.Answers.CALLS_REAL_METHODS);
        when(service.getProvider()).thenReturn(EVcsProvider.GITHUB);
        Project project = mock(Project.class);
        AnalysisProcessRequest request = mock(AnalysisProcessRequest.class);

        assertThatThrownBy(() -> service.buildExactAiAnalysisRequests(
                project,
                request,
                Optional.<CodeAnalysis>empty(),
                List.of()))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("GITHUB");
        assertThatThrownBy(() -> service.buildExactAiAnalysisRequests(
                project,
                request,
                Optional.<CodeAnalysis>empty(),
                List.of(),
                ignored -> { }))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("GITHUB");
    }
}

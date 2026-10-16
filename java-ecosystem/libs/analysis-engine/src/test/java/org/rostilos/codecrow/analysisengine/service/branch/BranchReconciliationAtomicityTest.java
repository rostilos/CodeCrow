package org.rostilos.codecrow.analysisengine.service.branch;

import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.service.*;
import org.rostilos.codecrow.analysisengine.service.vcs.*;
import org.rostilos.codecrow.core.model.branch.*;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.*;
import org.rostilos.codecrow.core.persistence.repository.branch.*;
import org.rostilos.codecrow.filecontent.service.FileSnapshotService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.springframework.test.util.ReflectionTestUtils;
import java.io.IOException;
import java.util.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class BranchReconciliationAtomicityTest {
    @ParameterizedTest @ValueSource(booleans={false,true})
    void failedOrDegradedChunkCannotPublishResolutionsFromThisOperation(boolean degraded) throws Exception {
        var issues=mock(BranchIssueRepository.class); var branches=mock(BranchRepository.class);
        var factory=mock(VcsServiceFactory.class); var ai=mock(AiAnalysisClient.class);
        var service=new BranchIssueReconciliationService(issues,branches,mock(FileSnapshotService.class),factory,
                mock(VcsClientProvider.class),ai,mock(IssueReconciliationEngine.class),mock(AstScopeEnricher.class));
        var branch=new Branch(); ReflectionTestUtils.setField(branch,"id",1L); branch.setBranchName("main");
        var issue=new BranchIssue(); ReflectionTestUtils.setField(issue,"id",10L); issue.setBranch(branch); issue.setFilePath("src/a.py");
        when(issues.findUnresolvedByBranchIdAndFilePath(1L,"src/a.py")).thenReturn(List.of(issue));
        when(issues.findByBranchIdAndOriginIssueId(1L,10L)).thenReturn(Optional.of(issue));
        when(branches.findByIdWithIssues(1L)).thenReturn(Optional.of(branch));
        var project=mock(Project.class); var repo=mock(VcsRepoInfo.class); var connection=mock(VcsConnection.class);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repo); when(repo.getVcsConnection()).thenReturn(connection);
        when(connection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
        var builder=mock(VcsAiClientService.class); when(factory.getAiClientService(any())).thenReturn(builder);
        var first=mock(AiAnalysisRequest.class); var second=mock(AiAnalysisRequest.class);
        when(builder.buildAiAnalysisRequestsForBranchReconciliation(any(),any(),anyList(),anyMap(),any()))
                .thenReturn(List.of(first,second));
        when(ai.performAnalysis(eq(first),any())).thenReturn(Map.of("issues",List.of(Map.of("id",10L,"isResolved",true))));
        if (degraded) when(ai.performAnalysis(eq(second),any())).thenReturn(Map.of("issues",List.of(),"reconciliationStatus","degraded"));
        else when(ai.performAnalysis(eq(second),any())).thenThrow(new IOException("second model chunk failed"));
        var request=new BranchProcessRequest(); request.targetBranchName="main"; request.commitHash="head";
        service.reanalyzeCandidateIssues(Set.of("src/a.py"),Set.of("src/a.py"),branch,project,request,
                ignored -> {},Map.of(),null,false,Runnable::run);
        assertThat(issue.isResolved()).isFalse();
        verify(issues,never()).save(any());
    }
}

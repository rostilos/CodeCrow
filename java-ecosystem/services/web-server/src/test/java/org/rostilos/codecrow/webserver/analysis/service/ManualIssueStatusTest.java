package org.rostilos.codecrow.webserver.analysis.service;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.qualitygate.QualityGateRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.springframework.test.util.ReflectionTestUtils;
import java.util.Optional;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

class ManualIssueStatusTest {
    @Test
    void reopenRetainsExplicitDecisionAfterResolutionMetadataIsCleared() {
        var repository = mock(CodeAnalysisIssueRepository.class);
        var issue = new CodeAnalysisIssue();
        ReflectionTestUtils.setField(issue, "id", 1L);
        when(repository.findByIdWithAnalysis(1L)).thenReturn(Optional.of(issue));
        var service = new AnalysisService(mock(CodeAnalysisService.class), repository,
                mock(BranchIssueRepository.class), mock(BranchRepository.class),
                mock(CodeAnalysisRepository.class), mock(QualityGateRepository.class));
        service.updateIssueStatus(1L, true, "Accepted risk", null);
        service.updateIssueStatus(1L, false, "Investigate again", null);
        assertThat(issue.isResolved()).isFalse();
        assertThat(issue.getResolvedAt()).isNull();
        assertThat(ReflectionTestUtils.getField(issue, "manualStatusResolved")).isEqualTo(false);
        assertThat(ReflectionTestUtils.getField(issue, "manualStatusUpdatedAt")).isNotNull();
    }
}

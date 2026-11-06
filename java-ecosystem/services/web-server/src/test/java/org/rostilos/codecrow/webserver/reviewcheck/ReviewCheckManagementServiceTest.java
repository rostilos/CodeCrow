package org.rostilos.codecrow.webserver.reviewcheck;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.reviewcheck.*;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.reviewcheck.ReviewCheckFindingRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.core.service.reviewcheck.ReviewCheckResultService;
import org.springframework.web.server.ResponseStatusException;
import java.util.List;
import java.util.Optional;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class ReviewCheckManagementServiceTest {
    final CodeAnalysisRepository analyses = mock(CodeAnalysisRepository.class);
    final ReviewCheckFindingRepository findings = mock(ReviewCheckFindingRepository.class);
    final ReviewCheckResultService results = mock(ReviewCheckResultService.class);
    final CodeAnalysisService analysisService = mock(CodeAnalysisService.class);
    final ReviewCheckManagementService service = new ReviewCheckManagementService(analyses, findings, results, analysisService);

    @Test void requestedVersionAndLatestAreProjectAndPrScoped() {
        var analysis = mock(CodeAnalysis.class); when(analysis.getPrVersion()).thenReturn(3);
        when(analyses.findByProjectIdAndPrNumberAndPrVersion(10L, 42L, 3)).thenReturn(Optional.of(analysis));
        when(analyses.findByProjectIdAndPrNumberWithMaxPrVersion(10L, 42L)).thenReturn(Optional.of(analysis));
        assertThat(service.get(10L, 42L, 3).version()).isEqualTo(3);
        assertThat(service.get(10L, 42L, 0).version()).isEqualTo(3);
        assertThatThrownBy(() -> service.get(11L, 42L, 3)).isInstanceOf(ResponseStatusException.class);
        verify(results, times(2)).findForAnalysis(analysis);
    }
    @Test void statusCannotResolveFindingFromAnotherProject() {
        assertThatThrownBy(() -> service.updateStatus(11L, 7L, true, "Accepted risk"))
                .isInstanceOf(ResponseStatusException.class);
        verify(findings).findByIdAndResult_Analysis_Project_Id(7L, 11L);
        verifyNoMoreInteractions(findings);
        verifyNoInteractions(analysisService);
    }
    @Test void manualDecisionOnlyPropagatesAcrossMatchingCheckHistoryInTheSamePr() {
        var first = finding(1L); var second = finding(2L);
        when(findings.findByIdAndResult_Analysis_Project_Id(7L, 10L)).thenReturn(Optional.of(second));
        when(findings.findByResult_Analysis_Project_IdAndResult_Analysis_PrNumberAndResult_CheckIdAndFindingKey(
                10L, 42L, "composer-audit", "advisory-key")).thenReturn(List.of(first, second));
        service.updateStatus(10L, 7L, true, "Accepted risk");
        for (var finding : List.of(first, second)) {
            assertThat(finding.isResolved()).isTrue(); assertThat(finding.getManualStatusResolved()).isTrue();
            assertThat(finding.getManualStatusUpdatedAt()).isNotNull();
            assertThat(finding.getResolvedDescription()).isEqualTo("Accepted risk");
            verify(analysisService).saveAnalysis(finding.getResult().getAnalysis());
        }
        verify(findings).saveAllAndFlush(List.of(first, second));
        service.updateStatus(10L, 7L, false, "Reopened");
        assertThat(first.isResolved()).isFalse(); assertThat(second.isResolved()).isFalse();
    }
    private ReviewCheckFinding finding(Long analysisId) {
        var analysis = mock(CodeAnalysis.class); when(analysis.getId()).thenReturn(analysisId); when(analysis.getPrNumber()).thenReturn(42L);
        var result = new ReviewCheckResult(); result.setAnalysis(analysis); result.setCheckId("composer-audit");
        var finding = new ReviewCheckFinding(); finding.setFindingKey("advisory-key"); result.addFinding(finding); return finding;
    }
}

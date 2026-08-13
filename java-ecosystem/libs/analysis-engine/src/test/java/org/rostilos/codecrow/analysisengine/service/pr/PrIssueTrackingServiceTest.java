package org.rostilos.codecrow.analysisengine.service.pr;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.service.AstScopeEnricher;
import org.rostilos.codecrow.analysisengine.service.IssueReconciliationEngine;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueScope;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.core.util.tracking.IssueFingerprint;
import org.rostilos.codecrow.core.util.tracking.PrIssueLineageFingerprint;
import org.rostilos.codecrow.core.util.tracking.TrackingConfidence;

import java.lang.reflect.Field;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class PrIssueTrackingServiceTest {

    @Mock private CodeAnalysisIssueRepository issueRepository;
    @Mock private IssueReconciliationEngine reconciliationEngine;
    @Mock private AstScopeEnricher astScopeEnricher;

    private PrIssueTrackingService service;
    private Project project;

    @BeforeEach
    void setUp() throws Exception {
        service = new PrIssueTrackingService(issueRepository, reconciliationEngine, astScopeEnricher);
        project = new Project();
        setId(project, 7L);
    }

    @Test
    void issueMissingFromIntermediateRuns_matchesItsAllRunActiveTipDespiteCategoryDrift() throws Exception {
        CodeAnalysis run1 = analysis(10L, 1, "run-1");
        CodeAnalysis run4 = analysis(40L, 4, "run-4");
        CodeAnalysisIssue historical = issue(100L, run1, "src/App.java", 12,
                "dangerous();", "stable-line", "Unsafe call", IssueCategory.SECURITY);
        CodeAnalysisIssue current = issue(400L, run4, "src/App.java", 12,
                "dangerous();", "stable-line", "Unsafe call", IssueCategory.BUG_RISK);
        String lineage = PrIssueLineageFingerprint.computePersisted(historical);
        historical.setLineageFingerprint(lineage);
        current.setLineageFingerprint(lineage);

        when(issueRepository.findByProjectIdAndPrNumber(7L, 42L))
                .thenReturn(List.of(historical, current));
        when(issueRepository.save(any(CodeAnalysisIssue.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        PrIssueTrackingService.TrackingSummary summary = service.trackPrIteration(
                run4, null, Map.of(), Map.of());

        assertThat(current.getTrackedFromIssueId()).isEqualTo(100L);
        assertThat(current.getTrackingConfidence()).isEqualTo(TrackingConfidence.EXACT);
        assertThat(summary.matchedCount()).isOne();
        assertThat(summary.newIssueCount()).isZero();
    }

    @Test
    void omittedHistoricalTip_remainsUnresolvedAndIsNotCopiedIntoCurrentRun() throws Exception {
        CodeAnalysis run1 = analysis(10L, 1, "run-1");
        CodeAnalysis run4 = analysis(40L, 4, "run-4");
        CodeAnalysisIssue historical = issue(100L, run1, "src/App.java", 12,
                "dangerous();", "old-line", "Unsafe call", IssueCategory.BUG_RISK);
        historical.setLineageFingerprint(PrIssueLineageFingerprint.computePersisted(historical));

        when(issueRepository.findByProjectIdAndPrNumber(7L, 42L))
                .thenReturn(List.of(historical));

        PrIssueTrackingService.TrackingSummary summary = service.trackPrIteration(
                run4, null, Map.of("src/App.java", "safe();"), Map.of());

        assertThat(historical.isResolved()).isFalse();
        assertThat(run4.getIssues()).isEmpty();
        assertThat(summary.resolvedCount()).isZero();
        verify(issueRepository, never()).save(historical);
    }

    @Test
    void closedHistoricalTip_doesNotCaptureARecurrence() throws Exception {
        CodeAnalysis run1 = analysis(10L, 1, "run-1");
        CodeAnalysis run4 = analysis(40L, 4, "run-4");
        CodeAnalysisIssue historical = issue(100L, run1, "src/App.java", 12,
                "dangerous();", "stable-line", "Unsafe call", IssueCategory.BUG_RISK);
        historical.setResolved(true);
        historical.setLineageFingerprint(PrIssueLineageFingerprint.computePersisted(historical));
        CodeAnalysisIssue recurrence = issue(400L, run4, "src/App.java", 12,
                "dangerous();", "stable-line", "Unsafe call", IssueCategory.SECURITY);
        recurrence.setLineageFingerprint(historical.getLineageFingerprint());

        when(issueRepository.findByProjectIdAndPrNumber(7L, 42L))
                .thenReturn(List.of(historical, recurrence));

        PrIssueTrackingService.TrackingSummary summary = service.trackPrIteration(
                run4, null, Map.of(), Map.of());

        assertThat(recurrence.getTrackedFromIssueId()).isNull();
        assertThat(summary.newIssueCount()).isOne();
    }

    @Test
    void ambiguousExactHistoricalTips_leaveCurrentFindingAsANewRoot() throws Exception {
        CodeAnalysis run1 = analysis(10L, 1, "run-1");
        CodeAnalysis run2 = analysis(20L, 2, "run-2");
        CodeAnalysis run4 = analysis(40L, 4, "run-4");
        CodeAnalysisIssue first = issue(100L, run1, "src/App.java", 12,
                "dangerous();", "stable-line", "Unsafe call", IssueCategory.BUG_RISK);
        CodeAnalysisIssue second = issue(200L, run2, "src/App.java", 12,
                "dangerous();", "stable-line", "Unsafe call", IssueCategory.SECURITY);
        CodeAnalysisIssue current = issue(400L, run4, "src/App.java", 12,
                "dangerous();", "stable-line", "Unsafe call", IssueCategory.CODE_QUALITY);
        String lineage = PrIssueLineageFingerprint.computePersisted(first);
        first.setLineageFingerprint(lineage);
        second.setLineageFingerprint(lineage);
        current.setLineageFingerprint(lineage);

        when(issueRepository.findByProjectIdAndPrNumber(7L, 42L))
                .thenReturn(List.of(first, second, current));

        PrIssueTrackingService.TrackingSummary summary = service.trackPrIteration(
                run4, null, Map.of(), Map.of());

        assertThat(current.getTrackedFromIssueId()).isNull();
        assertThat(summary.newIssueCount()).isOne();
    }

    @Test
    void distinctVerifiedFindingsAtNearbyLines_areNeverCollapsedByTracking() throws Exception {
        CodeAnalysis currentRun = analysis(40L, 4, "run-4");
        CodeAnalysisIssue first = issue(401L, currentRun, "src/App.java", 12,
                "first();", "first-line", "First defect", IssueCategory.BUG_RISK);
        CodeAnalysisIssue second = issue(402L, currentRun, "src/App.java", 13,
                "second();", "second-line", "Second defect", IssueCategory.BUG_RISK);

        when(issueRepository.findByProjectIdAndPrNumber(7L, 42L))
                .thenReturn(List.of(first, second));

        PrIssueTrackingService.TrackingSummary summary = service.trackPrIteration(
                currentRun, null, Map.of(), Map.of());

        assertThat(currentRun.getIssues()).containsExactly(first, second);
        assertThat(summary.newIssueCount()).isEqualTo(2);
        assertThat(first.getTrackedFromIssueId()).isNull();
        assertThat(second.getTrackedFromIssueId()).isNull();
    }

    private CodeAnalysis analysis(Long id, int prVersion, String commitHash) throws Exception {
        CodeAnalysis analysis = new CodeAnalysis();
        setId(analysis, id);
        analysis.setProject(project);
        analysis.setPrNumber(42L);
        analysis.setPrVersion(prVersion);
        analysis.setCommitHash(commitHash);
        return analysis;
    }

    private static CodeAnalysisIssue issue(
            Long id,
            CodeAnalysis analysis,
            String filePath,
            int line,
            String snippet,
            String lineHash,
            String title,
            IssueCategory category
    ) throws Exception {
        CodeAnalysisIssue issue = new CodeAnalysisIssue();
        setId(issue, id);
        issue.setFilePath(filePath);
        issue.setLineNumber(line);
        issue.setCodeSnippet(snippet);
        issue.setLineHash(lineHash);
        issue.setTitle(title);
        issue.setReason(title + " has an observable runtime impact");
        issue.setSeverity(IssueSeverity.HIGH);
        issue.setIssueCategory(category);
        issue.setIssueScope(IssueScope.LINE);
        issue.setIssueFingerprint(IssueFingerprint.compute(category, lineHash, title));
        issue.setContentFingerprint(IssueFingerprint.computeContentFingerprint(lineHash, title));
        analysis.addIssue(issue);
        return issue;
    }

    private static void setId(Object target, Long id) throws Exception {
        Field field = target.getClass().getDeclaredField("id");
        field.setAccessible(true);
        field.set(target, id);
    }
}

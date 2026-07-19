package org.rostilos.codecrow.analysisengine.service.pr;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.service.AstScopeEnricher;
import org.rostilos.codecrow.analysisengine.service.IssueReconciliationEngine;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiRequestPreviousIssueDTO;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.core.util.tracking.IssueFingerprint;
import org.rostilos.codecrow.core.util.tracking.LineHashSequence;

import java.lang.reflect.Field;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class PrIssueTrackingServiceTest {

    @Mock private CodeAnalysisIssueRepository issueRepository;
    @Mock private IssueReconciliationEngine reconciliationEngine;

    private AstScopeEnricher astScopeEnricher;
    private PrIssueTrackingService service;

    @BeforeEach
    void setUp() {
        astScopeEnricher = new AstScopeEnricher();
        service = new PrIssueTrackingService(issueRepository, reconciliationEngine, astScopeEnricher);
    }

    @AfterEach
    void tearDown() {
        astScopeEnricher.close();
    }

    @Test
    void omittedPreviousIssue_shouldStayUnresolvedWhenAnchorStillExists() throws Exception {
        String previousContent = "class App {\n  riskyCall();\n}\n";
        String currentContent = "// shifted\nclass App {\n  riskyCall();\n}\n";
        CodeAnalysis previous = analysis(10L, 42L, 1, "old");
        CodeAnalysis current = analysis(11L, 42L, 2, "new");
        CodeAnalysisIssue prevIssue = issue(100L, "src/App.txt", 2, "riskyCall();", previousContent);
        previous.addIssue(prevIssue);

        PrIssueTrackingService.TrackingSummary summary = service.trackPrIteration(
                current, previous,
                Map.of("src/App.txt", currentContent),
                Map.of("src/App.txt", previousContent));

        assertThat(prevIssue.isResolved()).isFalse();
        assertThat(summary.resolvedCount()).isZero();
        verify(issueRepository, never()).save(prevIssue);
    }

    @Test
    void omittedPreviousIssue_shouldResolveWhenAnchorDisappears() throws Exception {
        String previousContent = "class App {\n  riskyCall();\n}\n";
        String currentContent = "class App {\n  safeCall();\n}\n";
        CodeAnalysis previous = analysis(10L, 42L, 1, "old");
        CodeAnalysis current = analysis(11L, 42L, 2, "new");
        CodeAnalysisIssue prevIssue = issue(100L, "src/App.txt", 2, "riskyCall();", previousContent);
        previous.addIssue(prevIssue);

        when(issueRepository.save(any(CodeAnalysisIssue.class))).thenAnswer(inv -> inv.getArgument(0));

        PrIssueTrackingService.TrackingSummary summary = service.trackPrIteration(
                current, previous,
                Map.of("src/App.txt", currentContent),
                Map.of("src/App.txt", previousContent));

        assertThat(prevIssue.isResolved()).isTrue();
        assertThat(prevIssue.getResolvedByPr()).isEqualTo(42L);
        assertThat(prevIssue.getResolvedCommitHash()).isEqualTo("new");
        assertThat(prevIssue.getResolvedAnalysisId()).isEqualTo(11L);
        assertThat(prevIssue.getResolvedDescription()).contains("anchor removed");
        assertThat(summary.resolvedCount()).isEqualTo(1);
        verify(issueRepository).save(prevIssue);
    }

    @Test
    void candidatePreviousFindingResolutionRequiresBoundSnapshotAndExactReceipt() throws Exception {
        CodeAnalysis previous = candidateAnalysis(10L, 7L, 42L, 1, "a".repeat(40), null);
        CodeAnalysis current = candidateAnalysis(
                11L, 7L, 42L, 2, "b".repeat(40), "candidate-execution-1");
        CodeAnalysisIssue prevIssue = issue(
                100L, "src/App.txt", 2, "riskyCall();", "class App {\n  riskyCall();\n}\n");
        previous.addIssue(prevIssue);
        AiRequestPreviousIssueDTO bound = AiRequestPreviousIssueDTO.fromEntity(prevIssue);
        when(issueRepository.findByIdWithAnalysis(100L)).thenReturn(Optional.of(prevIssue));
        when(issueRepository.save(prevIssue)).thenReturn(prevIssue);
        Map<String, Object> resolvedDecision = decision(
                "100", "RESOLVED", List.of(exactReceipt(
                        "candidate-execution-1", "b".repeat(40))));

        PrIssueTrackingService.CandidateTrackingSummary summary =
                service.reconcileCandidatePreviousFindings(
                        current,
                        List.of(bound),
                        agenticReview(List.of(resolvedDecision)),
                        "candidate-execution-1",
                        "b".repeat(40));

        assertThat(summary.totalCount()).isEqualTo(1);
        assertThat(summary.resolvedCount()).isEqualTo(1);
        assertThat(summary.stillPresentCount()).isZero();
        assertThat(summary.inconclusiveCount()).isZero();
        assertThat(summary.rejectedCount()).isZero();
        assertThat(prevIssue.isResolved()).isTrue();
        assertThat(prevIssue.getResolvedByPr()).isEqualTo(42L);
        assertThat(prevIssue.getResolvedCommitHash()).isEqualTo("b".repeat(40));
        assertThat(prevIssue.getResolvedAnalysisId()).isEqualTo(11L);
        assertThat(prevIssue.getResolvedBy()).isEqualTo("agentic-review");
        assertThat(prevIssue.getResolvedDescription()).contains("exact source proves");
        verify(issueRepository).save(prevIssue);
    }

    @Test
    void candidatePreviousFindingCannotMutateForeignDuplicateOrUnprovenIssue() throws Exception {
        CodeAnalysis previous = candidateAnalysis(10L, 7L, 42L, 1, "a".repeat(40), null);
        CodeAnalysis current = candidateAnalysis(
                11L, 7L, 42L, 2, "b".repeat(40), "candidate-execution-1");
        CodeAnalysisIssue prevIssue = issue(
                100L, "src/App.txt", 2, "riskyCall();", "class App {\n  riskyCall();\n}\n");
        previous.addIssue(prevIssue);
        AiRequestPreviousIssueDTO bound = AiRequestPreviousIssueDTO.fromEntity(prevIssue);

        Map<String, Object> first = decision("100", "RESOLVED", List.of());
        Map<String, Object> duplicate = decision(
                "100", "STILL_PRESENT", List.of(exactReceipt(
                        "candidate-execution-1", "b".repeat(40))));
        Map<String, Object> foreign = decision(
                "999", "RESOLVED", List.of(exactReceipt(
                        "candidate-execution-1", "b".repeat(40))));

        PrIssueTrackingService.CandidateTrackingSummary summary =
                service.reconcileCandidatePreviousFindings(
                        current,
                        List.of(bound),
                        agenticReview(List.of(first, duplicate, foreign)),
                        "candidate-execution-1",
                        "b".repeat(40));

        assertThat(summary.totalCount()).isEqualTo(1);
        assertThat(summary.inconclusiveCount()).isEqualTo(1);
        assertThat(summary.rejectedCount()).isEqualTo(2);
        assertThat(prevIssue.isResolved()).isFalse();
        verify(issueRepository, never()).findByIdWithAnalysis(anyLong());
        verify(issueRepository, never()).save(any());
    }

    @Test
    void candidatePreviousFindingFailsClosedWhenPersistedIssueNoLongerMatchesBoundSnapshot() throws Exception {
        CodeAnalysis previous = candidateAnalysis(10L, 7L, 42L, 1, "a".repeat(40), null);
        CodeAnalysis current = candidateAnalysis(
                11L, 7L, 42L, 2, "b".repeat(40), "candidate-execution-1");
        CodeAnalysisIssue prevIssue = issue(
                100L, "src/App.txt", 2, "riskyCall();", "class App {\n  riskyCall();\n}\n");
        previous.addIssue(prevIssue);
        AiRequestPreviousIssueDTO bound = AiRequestPreviousIssueDTO.fromEntity(prevIssue);
        prevIssue.setTitle("Changed after immutable request acquisition");
        when(issueRepository.findByIdWithAnalysis(100L)).thenReturn(Optional.of(prevIssue));
        Map<String, Object> resolvedDecision = decision(
                "100", "RESOLVED", List.of(exactReceipt(
                        "candidate-execution-1", "b".repeat(40))));

        PrIssueTrackingService.CandidateTrackingSummary summary =
                service.reconcileCandidatePreviousFindings(
                        current,
                        List.of(bound),
                        agenticReview(List.of(resolvedDecision)),
                        "candidate-execution-1",
                        "b".repeat(40));

        assertThat(summary.inconclusiveCount()).isEqualTo(1);
        assertThat(summary.resolvedCount()).isZero();
        assertThat(prevIssue.isResolved()).isFalse();
        verify(issueRepository, never()).save(any());
    }

    @Test
    void candidatePreviousFindingCannotUseExactReceiptFromUnrelatedPath() throws Exception {
        CodeAnalysis previous = candidateAnalysis(10L, 7L, 42L, 1, "a".repeat(40), null);
        CodeAnalysis current = candidateAnalysis(
                11L, 7L, 42L, 2, "b".repeat(40), "candidate-execution-1");
        CodeAnalysisIssue prevIssue = issue(
                100L, "src/App.txt", 2, "riskyCall();", "class App {\n  riskyCall();\n}\n");
        previous.addIssue(prevIssue);
        AiRequestPreviousIssueDTO bound = AiRequestPreviousIssueDTO.fromEntity(prevIssue);
        lenient().when(issueRepository.findByIdWithAnalysis(100L))
                .thenReturn(Optional.of(prevIssue));
        lenient().when(issueRepository.save(prevIssue)).thenReturn(prevIssue);
        Map<String, Object> resolvedDecision = decision(
                "100", "RESOLVED", List.of(exactReceipt(
                        "candidate-execution-1", "b".repeat(40), "src/Unrelated.txt")));

        PrIssueTrackingService.CandidateTrackingSummary summary =
                service.reconcileCandidatePreviousFindings(
                        current,
                        List.of(bound),
                        agenticReview(List.of(resolvedDecision)),
                        "candidate-execution-1",
                        "b".repeat(40));

        assertThat(summary.inconclusiveCount()).isEqualTo(1);
        assertThat(summary.resolvedCount()).isZero();
        assertThat(prevIssue.isResolved()).isFalse();
        verify(issueRepository, never()).findByIdWithAnalysis(anyLong());
        verify(issueRepository, never()).save(any());
    }

    @Test
    void candidateStillPresentDecisionIsAvailableWithoutCreatingOrResolvingIssue() throws Exception {
        CodeAnalysis previous = candidateAnalysis(10L, 7L, 42L, 1, "a".repeat(40), null);
        CodeAnalysis current = candidateAnalysis(
                11L, 7L, 42L, 2, "b".repeat(40), "candidate-execution-1");
        CodeAnalysisIssue prevIssue = issue(
                100L, "src/App.txt", 2, "riskyCall();", "class App {\n  riskyCall();\n}\n");
        previous.addIssue(prevIssue);
        AiRequestPreviousIssueDTO bound = AiRequestPreviousIssueDTO.fromEntity(prevIssue);
        when(issueRepository.findByIdWithAnalysis(100L)).thenReturn(Optional.of(prevIssue));
        Map<String, Object> stillPresentDecision = decision(
                "100", "STILL_PRESENT", List.of(exactReceipt(
                        "candidate-execution-1", "b".repeat(40))));

        PrIssueTrackingService.CandidateTrackingSummary summary =
                service.reconcileCandidatePreviousFindings(
                        current,
                        List.of(bound),
                        agenticReview(List.of(stillPresentDecision)),
                        "candidate-execution-1",
                        "b".repeat(40));

        assertThat(summary.stillPresentCount()).isEqualTo(1);
        assertThat(summary.resolvedCount()).isZero();
        assertThat(prevIssue.isResolved()).isFalse();
        assertThat(current.getIssues()).isEmpty();
        verify(issueRepository, never()).save(any());
    }

    private static CodeAnalysis analysis(Long id, Long prNumber, int prVersion, String commitHash) throws Exception {
        CodeAnalysis analysis = new CodeAnalysis();
        setId(analysis, id);
        analysis.setPrNumber(prNumber);
        analysis.setPrVersion(prVersion);
        analysis.setCommitHash(commitHash);
        return analysis;
    }

    private static CodeAnalysis candidateAnalysis(
            Long id,
            Long projectId,
            Long prNumber,
            int prVersion,
            String commitHash,
            String executionId) throws Exception {
        CodeAnalysis analysis = analysis(id, prNumber, prVersion, commitHash);
        Project project = new Project();
        setId(project, projectId);
        analysis.setProject(project);
        if (executionId != null) {
            analysis.bindExecutionIdentity(executionId, "d".repeat(64));
        }
        return analysis;
    }

    private static Map<String, Object> agenticReview(List<Map<String, Object>> decisions) {
        return Map.of("previousFindingDecisions", decisions);
    }

    private static Map<String, Object> decision(
            String issueId,
            String status,
            List<Map<String, Object>> evidence) {
        return Map.of(
                "issueId", issueId,
                "status", status,
                "reason", "Registered exact source proves the previous root cause is addressed.",
                "evidence", evidence);
    }

    private static Map<String, Object> exactReceipt(String executionId, String headSha) {
        return exactReceipt(executionId, headSha, "src/App.txt");
    }

    private static Map<String, Object> exactReceipt(
            String executionId,
            String headSha,
            String path) {
        Map<String, Object> receipt = new LinkedHashMap<>();
        receipt.put("kind", "exact_source_span_v1");
        receipt.put("execution_id", executionId);
        receipt.put("head_sha", headSha);
        receipt.put("snapshot_id", "snapshot-1");
        receipt.put("path", path);
        receipt.put("start_line", 1);
        receipt.put("end_line", 3);
        receipt.put("source_digest", "e".repeat(64));
        receipt.put("span_digest", "f".repeat(64));
        receipt.put("receipt_id", "c".repeat(64));
        return receipt;
    }

    private static CodeAnalysisIssue issue(Long id, String filePath, int line, String snippet, String content)
            throws Exception {
        LineHashSequence hashes = LineHashSequence.from(content);
        String lineHash = hashes.getHashForLine(line);

        CodeAnalysisIssue issue = new CodeAnalysisIssue();
        setId(issue, id);
        issue.setFilePath(filePath);
        issue.setLineNumber(line);
        issue.setCodeSnippet(snippet);
        issue.setLineHash(lineHash);
        issue.setLineHashContext(hashes.getContextHash(line, 2));
        issue.setTitle("Risky call");
        issue.setReason("Risky call remains");
        issue.setSeverity(IssueSeverity.HIGH);
        issue.setIssueCategory(IssueCategory.BUG_RISK);
        issue.setIssueFingerprint(IssueFingerprint.compute(IssueCategory.BUG_RISK, lineHash, "Risky call"));
        issue.setContentFingerprint(IssueFingerprint.computeContentFingerprint(lineHash, "Risky call"));
        return issue;
    }

    private static void setId(Object target, Long id) throws Exception {
        Field f = target.getClass().getDeclaredField("id");
        f.setAccessible(true);
        f.set(target, id);
    }
}

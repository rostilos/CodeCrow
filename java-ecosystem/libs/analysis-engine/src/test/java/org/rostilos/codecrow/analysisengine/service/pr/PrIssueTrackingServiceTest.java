package org.rostilos.codecrow.analysisengine.service.pr;

import org.junit.jupiter.api.AfterEach;
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
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.core.util.tracking.IssueFingerprint;
import org.rostilos.codecrow.core.util.tracking.LineHashSequence;

import java.lang.reflect.Field;
import java.util.List;
import java.util.Map;

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

    private static CodeAnalysis analysis(Long id, Long prNumber, int prVersion, String commitHash) throws Exception {
        CodeAnalysis analysis = new CodeAnalysis();
        setId(analysis, id);
        analysis.setPrNumber(prNumber);
        analysis.setPrVersion(prVersion);
        analysis.setCommitHash(commitHash);
        return analysis;
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

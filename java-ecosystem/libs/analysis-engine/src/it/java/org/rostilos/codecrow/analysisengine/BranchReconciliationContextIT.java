package org.rostilos.codecrow.analysisengine;

import jakarta.persistence.EntityManager;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.service.*;
import org.rostilos.codecrow.analysisengine.service.branch.BranchIssueReconciliationService;
import org.rostilos.codecrow.analysisengine.service.vcs.*;
import org.rostilos.codecrow.core.model.branch.*;
import org.rostilos.codecrow.core.model.codeanalysis.*;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.model.vcs.*;
import org.rostilos.codecrow.core.persistence.repository.branch.*;
import org.rostilos.codecrow.filecontent.service.FileSnapshotService;
import org.rostilos.codecrow.testsupport.base.IntegrationTest;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import java.util.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

@IntegrationTest
@SpringBootTest(classes=BranchFullReconciliationLeaseIT.Config.class,
        properties={"spring.flyway.enabled=false", "spring.jpa.hibernate.ddl-auto=create-drop"})
class BranchReconciliationContextIT {
    @Autowired EntityManager em;
    @Autowired PlatformTransactionManager transactions;
    @Autowired BranchRepository branches;
    @Autowired BranchIssueRepository issues;
    @Autowired AnalysisLockService locks;

    @ParameterizedTest @EnumSource(EVcsProvider.class)
    void guardFixClosesIssueAlthoughItsAnchorSurvives(EVcsProvider provider) throws Exception { exercise(provider, false); }

    @ParameterizedTest @EnumSource(EVcsProvider.class)
    void relatedFileFixClosesIssueWithoutChangingItsPrimaryFile(EVcsProvider provider) throws Exception { exercise(provider, true); }

    @ParameterizedTest @EnumSource(EVcsProvider.class)
    void secondModelChunkFailureCannotPublishPartialResolutions(EVcsProvider provider) throws Exception {
        exercise(provider, false, true);
    }

    @ParameterizedTest @EnumSource(EVcsProvider.class)
    void leaseReplacedWhileModelRunsCannotPublishAnyDecision(EVcsProvider provider) throws Exception {
        exercise(provider, false, false, true);
    }

    private void exercise(EVcsProvider provider, boolean relatedChange) throws Exception {
        exercise(provider, relatedChange, false);
    }

    private void exercise(EVcsProvider provider, boolean relatedChange, boolean failSecondChunk) throws Exception {
        exercise(provider, relatedChange, failSecondChunk, false);
    }

    private void exercise(EVcsProvider provider, boolean relatedChange, boolean failSecondChunk, boolean loseLease) throws Exception {
        var tx = new TransactionTemplate(transactions);
        Branch branch = tx.execute(status -> {
            String name = UUID.randomUUID().toString();
            var workspace = new Workspace(name, name, "context test"); em.persist(workspace);
            var project = new Project(); project.setWorkspace(workspace); project.setNamespace(name); em.persist(project);
            var value = new Branch(); value.setProject(project); value.setBranchName("main"); value.setCommitHash("head"); em.persist(value);
            persistIssue(value, "src/service.py", "Validation belongs in src/guard.py before dangerous(value).");
            persistIssue(value, "src/unrelated.py", "Separate unrelated finding.");
            if (failSecondChunk) persistIssue(value, "src/service.py", "Another defect in the changed caller.");
            value.updateIssueCounts();
            return value;
        });
        BranchIssue selected = issues.findUnresolvedByBranchIdAndFilePath(branch.getId(), "src/service.py").get(0);
        String ownerKey = loseLease ? locks.acquireLock(branch.getProject(), "main",
                org.rostilos.codecrow.core.model.analysis.AnalysisLockType.BRANCH_ANALYSIS).orElseThrow() : null;
        var replacement = new java.util.concurrent.atomic.AtomicReference<String>();
        var project = mock(Project.class);
        when(project.getId()).thenReturn(branch.getProject().getId());
        var repo = mock(VcsRepoInfo.class); var connection = mock(VcsConnection.class);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repo); when(repo.getVcsConnection()).thenReturn(connection);
        when(connection.getProviderType()).thenReturn(provider);
        var factory = mock(VcsServiceFactory.class); var builder = mock(VcsAiClientService.class);
        var client = mock(AiAnalysisClient.class); var requestDto = mock(AiAnalysisRequest.class);
        when(factory.getAiClientService(provider)).thenReturn(builder);
        var secondRequest = mock(AiAnalysisRequest.class);
        when(builder.buildAiAnalysisRequestsForBranchReconciliation(any(), any(), anyList(), anyMap(), any()))
                .thenReturn(failSecondChunk ? List.of(requestDto, secondRequest) : List.of(requestDto));
        if (failSecondChunk) when(client.performAnalysis(eq(secondRequest), any()))
                .thenThrow(new java.io.IOException("second chunk unavailable"));
        when(client.performAnalysis(eq(requestDto), any())).thenAnswer(inv -> {
            if (loseLease) {
                locks.releaseLock(ownerKey);
                replacement.set(locks.acquireLock(branch.getProject(), "main",
                        org.rostilos.codecrow.core.model.analysis.AnalysisLockType.BRANCH_ANALYSIS).orElseThrow());
            }
            return Map.of("issues", List.of(Map.of("id", selected.getId(), "isResolved", true,
                    "resolutionReason", "The guard now rejects invalid input before the call.")));
        });
        String changed = relatedChange ? "src/guard.py" : "src/service.py";
        String source = relatedChange ? "def run(value):\n    dangerous(value)\n"
                : "def run(value):\n    if value is None:\n        return\n    dangerous(value)\n";
        String diff = "diff --git a/"+changed+" b/"+changed+"\n--- a/"+changed+"\n+++ b/"+changed+"\n@@ -1 +1,2 @@\n context\n+guard(value)\n";
        var request = new BranchProcessRequest(); request.targetBranchName="main"; request.commitHash="head";
        var ast = new AstScopeEnricher();
        try {
            var service = new BranchIssueReconciliationService(issues, branches, mock(FileSnapshotService.class),
                    factory, mock(VcsClientProvider.class), client, new IssueReconciliationEngine(), ast);
            service.reanalyzeCandidateIssues(Set.of(changed), Set.of("src/service.py", "src/guard.py"), branch, project,
                    request, ignored -> {}, Map.of("src/service.py", source, "src/guard.py", "validate(value)"), diff, false,
                    action -> {
                        if (loseLease) locks.withOwnedLocks(List.of(ownerKey), () -> { action.run(); return null; });
                        else tx.executeWithoutResult(status -> action.run());
                    });
        } finally {
            ast.close();
            if (replacement.get() != null) locks.releaseLock(replacement.get());
            if (ownerKey != null) locks.releaseLock(ownerKey);
        }
        assertThat(issues.findById(selected.getId()).orElseThrow().isResolved()).isEqualTo(!failSecondChunk && !loseLease);
        assertThat(issues.findUnresolvedByBranchIdAndFilePath(branch.getId(), "src/unrelated.py")).hasSize(1);
        assertThat(branches.findById(branch.getId()).orElseThrow().getTotalIssues()).isEqualTo(failSecondChunk ? 3 : loseLease ? 2 : 1);
        verify(builder).buildAiAnalysisRequestsForBranchReconciliation(eq(project), eq(request),
                argThat(previous -> previous.size()==(failSecondChunk ? 2 : 1)),
                argThat(contents -> contents.keySet().equals(Set.of("src/service.py", "src/guard.py"))), contains("+guard(value)"));
    }

    @org.junit.jupiter.api.Test
    void staleReconciliationSnapshotCannotOverwriteAManualDecision() {
        var issueId=new TransactionTemplate(transactions).execute(status -> {
            String key=UUID.randomUUID().toString(); var workspace=new Workspace(key,key,"branch write conflict"); em.persist(workspace);
            var project=new Project(); project.setWorkspace(workspace); project.setNamespace(key); em.persist(project);
            var branch=new Branch(); branch.setProject(project); branch.setBranchName("main"); em.persist(branch);
            var issue=new BranchIssue(); issue.setBranch(branch); issue.setFilePath("src/a.py");
            issue.setSeverity(IssueSeverity.HIGH); em.persist(issue); em.flush(); return issue.getId();
        });
        var stale=issues.findById(issueId).orElseThrow();
        var manual=issues.findById(issueId).orElseThrow(); manual.setResolved(true); manual.setResolvedBy("manual"); issues.saveAndFlush(manual);
        stale.setCurrentLineNumber(20);
        assertThatThrownBy(() -> issues.saveAndFlush(stale)).isInstanceOf(org.springframework.dao.OptimisticLockingFailureException.class);
        assertThat(issues.findById(issueId).orElseThrow().isResolved()).isTrue();
    }

    private void persistIssue(Branch branch, String path, String reason) {
        var issue = new BranchIssue(); issue.setBranch(branch); issue.setFilePath(path);
        issue.setLineNumber(2); issue.setCurrentLineNumber(2); issue.setCodeSnippet("dangerous(value)");
        issue.setReason(reason); issue.setTitle("Unchecked input"); issue.setSeverity(IssueSeverity.HIGH);
        issue.setIssueScope(IssueScope.LINE); branch.getIssues().add(issue); em.persist(issue);
    }
}

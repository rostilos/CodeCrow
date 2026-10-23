package org.rostilos.codecrow.analysisengine.service.branch;

import okhttp3.OkHttpClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.service.AstScopeEnricher;
import org.rostilos.codecrow.analysisengine.service.IssueReconciliationEngine;
import org.rostilos.codecrow.analysisengine.service.IssueReconciliationEngine.LineRemapResult;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.filecontent.service.FileSnapshotService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;

import java.lang.reflect.Field;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class BranchIssueReconciliationServiceTest {

    @Mock private BranchIssueRepository branchIssueRepository;
    @Mock private BranchRepository branchRepository;
    @Mock private FileSnapshotService fileSnapshotService;
    @Mock private VcsServiceFactory vcsServiceFactory;
    @Mock private VcsClientProvider vcsClientProvider;
    @Mock private AiAnalysisClient aiAnalysisClient;
    @Mock private IssueReconciliationEngine reconciliationEngine;
    @Mock private AstScopeEnricher astScopeEnricher;

    private BranchIssueReconciliationService service;

    @BeforeEach
    void setUp() {
        service = new BranchIssueReconciliationService(
                branchIssueRepository,
                branchRepository,
                fileSnapshotService,
                vcsServiceFactory,
                vcsClientProvider,
                aiAnalysisClient,
                reconciliationEngine,
                astScopeEnricher);
    }

    @Test void guardAddedAboveUnchangedAnchorStillGetsSemanticReconciliation() throws Exception {
        assertSemanticReconciliation(false);
    }

    @Test void acquisitionIncludesPrimaryAndReferencedFilesWithoutUnrelatedIssues() throws Exception {
        var branch = new Branch(); setId(branch, 1L);
        var related = new BranchIssue(); related.setFilePath("src/service.py");
        related.setReason("Validation in guard.py protects this caller.");
        var unrelated = new BranchIssue(); unrelated.setFilePath("src/noise.py");
        unrelated.setReason("Unrelated behavior.");
        when(branchIssueRepository.findAllUnresolvedByBranchId(1L)).thenReturn(List.of(related, unrelated));
        assertThat(service.findReconciliationPaths(branch, Set.of("guard.py")))
                .containsExactlyInAnyOrder("guard.py", "src/service.py");
        assertThat(service.findChangedFilesWithUnresolvedIssues(branch, Set.of("guard.py", "unrelated.py")))
                .containsExactly("guard.py");
    }

    @Test void relatedFileChangeReconcilesUnchangedPrimaryAndIncludesRelatedDiff() throws Exception {
        assertSemanticReconciliation(true);
    }

    private void assertSemanticReconciliation(boolean relatedChange) throws Exception {
        var branch = new Branch(); setId(branch, 1L); branch.setBranchName("main");
        var issue = new BranchIssue(); setId(issue, 10L); issue.setBranch(branch);
        issue.setFilePath("src/service.py"); issue.setLineNumber(2); issue.setCurrentLineNumber(2);
        issue.setCodeSnippet("dangerous(value)");
        issue.setIssueScope(org.rostilos.codecrow.core.model.codeanalysis.IssueScope.LINE);
        issue.setReason("Caller needs validation from src/guard.py before dangerous(value).");
        issue.setSeverity(org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity.HIGH);
        var unrelated = new BranchIssue(); unrelated.setFilePath("src/unrelated.py");
        String changed = relatedChange ? "src/guard.py" : "src/service.py";
        lenient().when(branchIssueRepository.findUnresolvedByBranchIdAndFilePath(1L, "src/service.py"))
                .thenReturn(List.of(issue));
        lenient().when(branchIssueRepository.findAllUnresolvedByBranchId(1L)).thenReturn(List.of(issue, unrelated));
        when(branchRepository.findByIdWithIssues(1L)).thenReturn(java.util.Optional.of(branch));
        var project = mock(Project.class);
        var repo = mock(org.rostilos.codecrow.core.model.vcs.VcsRepoInfo.class);
        var connection = mock(org.rostilos.codecrow.core.model.vcs.VcsConnection.class);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repo);
        lenient().when(repo.getVcsConnection()).thenReturn(connection);
        lenient().when(connection.getProviderType()).thenReturn(org.rostilos.codecrow.core.model.vcs.EVcsProvider.GITHUB);
        var aiBuilder = mock(org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService.class);
        var aiRequest = mock(org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest.class);
        lenient().when(vcsServiceFactory.getAiClientService(any())).thenReturn(aiBuilder);
        lenient().when(aiBuilder.buildAiAnalysisRequestsForBranchReconciliation(any(), any(), anyList(), anyMap(), any()))
                .thenReturn(List.of(aiRequest));
        lenient().when(aiAnalysisClient.performAnalysis(eq(aiRequest), any())).thenReturn(Map.of("issues", List.of()));
        var request = new BranchProcessRequest(); request.targetBranchName="main"; request.commitHash="head";
        String source = relatedChange ? "def run(value):\n    dangerous(value)\n"
                : "def run(value):\n    if value is None:\n        return\n    dangerous(value)\n";
        String diff = "diff --git a/"+changed+" b/"+changed+"\n--- a/"+changed+"\n+++ b/"+changed+"\n@@ -1 +1,2 @@\n context\n+guard(value)\n";
        var ast = new AstScopeEnricher();
        try {
            var realTracking = new BranchIssueReconciliationService(branchIssueRepository, branchRepository,
                    fileSnapshotService, vcsServiceFactory, vcsClientProvider, aiAnalysisClient,
                    new IssueReconciliationEngine(), ast);
            realTracking.reanalyzeCandidateIssues(Set.of(changed), Set.of("src/service.py", "src/guard.py"),
                    branch, project, request, ignored -> {}, Map.of("src/service.py", source, "src/guard.py", "validate(value)"), diff, false, Runnable::run);
        } finally { ast.close(); }
        var files = org.mockito.ArgumentCaptor.forClass(Map.class);
        verify(aiBuilder).buildAiAnalysisRequestsForBranchReconciliation(eq(project), eq(request),
                argThat(previous -> previous.size()==1 && previous.get(0).id().equals("10")), files.capture(), contains("+guard(value)"));
        assertThat(files.getValue()).containsKeys("src/service.py", "src/guard.py");
        assertThat(issue.isResolved()).isFalse();
    }

    @Test
    void reconcileIssueLineNumbers_shouldMigrateUnresolvedIssuesOnRename() throws Exception {
        Branch branch = new Branch();
        setId(branch, 1L);
        branch.setBranchName("main");

        BranchIssue issue = new BranchIssue();
        issue.setBranch(branch);
        issue.setFilePath("src/OldName.java");
        issue.setLineNumber(10);
        issue.setCurrentLineNumber(10);

        String rawDiff = """
                diff --git a/src/OldName.java b/src/NewName.java
                similarity index 91%
                rename from src/OldName.java
                rename to src/NewName.java
                index 1111111..2222222 100644
                --- a/src/OldName.java
                +++ b/src/NewName.java
                @@ -10,3 +12,4 @@
                 class NewName {
                +    void added() {}
                 }
                """;

        when(branchIssueRepository.findUnresolvedByBranchIdAndFilePath(1L, "src/OldName.java"))
                .thenReturn(List.of(issue));
        when(reconciliationEngine.remapLinesFromDiff(anyList(), contains("rename from src/OldName.java")))
                .thenReturn(List.of(new LineRemapResult(issue, 10, 12, null, null)));

        service.reconcileIssueLineNumbers(rawDiff,
                Set.of("src/OldName.java", "src/NewName.java"), branch);

        assertThat(issue.getFilePath()).isEqualTo("src/NewName.java");
        assertThat(issue.getCurrentLineNumber()).isEqualTo(12);
        verify(branchIssueRepository).save(issue);
    }

    @Test
    void findChangedFilesWithUnresolvedIssues_shouldReturnOnlyMatchingIssuePaths() throws Exception {
        Branch branch = new Branch();
        setId(branch, 1L);
        BranchIssue issue = new BranchIssue();
        issue.setFilePath("src/HasIssue.java");
        when(branchIssueRepository.findUnresolvedByBranchIdAndFilePath(eq(1L), anyString()))
                .thenAnswer(invocation -> "src/HasIssue.java".equals(invocation.getArgument(1)) ? List.of(issue) : List.of());

        Set<String> result = service.findChangedFilesWithUnresolvedIssues(
                branch, Set.of("src/HasIssue.java", "src/Unrelated.java"));

        assertThat(result).containsExactly("src/HasIssue.java");
    }

    @Test
    void reanalyzeCandidateIssues_shouldResolveDeletedFileIssues() throws Exception {
        Branch branch = new Branch();
        setId(branch, 1L);
        branch.setBranchName("main");
        Project project = new Project();

        BranchIssue issue = new BranchIssue();
        issue.setBranch(branch);
        issue.setFilePath("src/Deleted.java");
        issue.setResolved(false);

        BranchProcessRequest request = new BranchProcessRequest();
        request.targetBranchName = "main";
        request.commitHash = "abc123";
        request.sourcePrNumber = 42L;

        when(branchIssueRepository.findUnresolvedByBranchIdAndFilePath(1L, "src/Deleted.java"))
                .thenReturn(List.of(issue));
        when(branchRepository.findByIdWithIssues(1L)).thenReturn(java.util.Optional.of(branch));
        when(branchRepository.save(any(Branch.class))).thenAnswer(inv -> inv.getArgument(0));

        service.reanalyzeCandidateIssues(Set.of("src/Deleted.java"), Set.of(), branch, project,
                request, event -> { }, Map.of(), "diff --git a/src/Deleted.java b/src/Deleted.java\n", true, Runnable::run);

        assertThat(issue.isResolved()).isTrue();
        assertThat(issue.getResolvedInPrNumber()).isEqualTo(42L);
        assertThat(issue.getResolvedInCommitHash()).isEqualTo("abc123");
        assertThat(issue.getResolvedDescription()).contains("File deleted");
        verify(branchIssueRepository).saveAll(List.of(issue));
    }

    private static void setId(Object target, Long id) throws Exception {
        Field f = target.getClass().getDeclaredField("id");
        f.setAccessible(true);
        f.set(target, id);
    }

    @Test void missingHeadFileDoesNotResolveRemovalImpactAndDiffDoesNotRemapBase() throws Exception {
        Branch branch=new Branch(); setId(branch,1L); branch.setBranchName("main");
        BranchIssue issue=new BranchIssue(); issue.setBranch(branch); issue.setFilePath("src/Deleted.java");
        issue.setLineNumber(7); issue.setCurrentLineNumber(7);
        issue.setAnchorSide(org.rostilos.codecrow.core.model.codeanalysis.IssueAnchorSide.BASE);
        when(branchIssueRepository.findUnresolvedByBranchIdAndFilePath(1L,"src/Deleted.java"))
                .thenReturn(List.of(issue));
        BranchProcessRequest request=new BranchProcessRequest(); request.targetBranchName="main"; request.commitHash="head";
        // No optional provider/AI connection: degraded reconciliation retains issue.
        service.reanalyzeCandidateIssues(Set.of("src/Deleted.java"),Set.of(),branch,new Project(),
                request,event->{},Map.of(),"",false,Runnable::run);
        assertThat(issue.isResolved()).isFalse();
        service.reconcileIssueLineNumbers("diff --git a/src/Deleted.java b/src/Deleted.java\n--- a/src/Deleted.java\n+++ b/src/Deleted.java\n@@ -1 +1,2 @@\n+new\n context", Set.of("src/Deleted.java"),branch);
        assertThat(issue.getCurrentLineNumber()).isEqualTo(7);
        verifyNoInteractions(reconciliationEngine);
    }
}

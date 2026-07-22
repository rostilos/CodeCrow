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
        when(branchIssueRepository.findUnresolvedByBranchIdAndFilePaths(eq(1L), anyList()))
                .thenReturn(List.of(issue));

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
                request, event -> { }, Map.of(), "diff --git a/src/Deleted.java b/src/Deleted.java\n");

        assertThat(issue.isResolved()).isTrue();
        assertThat(issue.getResolvedInPrNumber()).isEqualTo(42L);
        assertThat(issue.getResolvedInCommitHash()).isEqualTo("abc123");
        assertThat(issue.getResolvedDescription()).contains("File deleted");
        verify(branchIssueRepository).save(issue);
    }

    private static void setId(Object target, Long id) throws Exception {
        Field f = target.getClass().getDeclaredField("id");
        f.setAccessible(true);
        f.set(target, id);
    }
}

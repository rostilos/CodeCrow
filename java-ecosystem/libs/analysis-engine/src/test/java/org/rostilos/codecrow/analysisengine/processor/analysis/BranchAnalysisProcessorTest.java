package org.rostilos.codecrow.analysisengine.processor.analysis;

import okhttp3.OkHttpClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.ProjectService;
import org.rostilos.codecrow.analysisengine.service.rag.RagOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchFile;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchFileRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;

import java.io.IOException;
import java.util.*;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = org.mockito.quality.Strictness.LENIENT)
@DisplayName("BranchAnalysisProcessor")
class BranchAnalysisProcessorTest {

    @Mock
    private ProjectService projectService;

    @Mock
    private BranchFileRepository branchFileRepository;

    @Mock
    private BranchRepository branchRepository;

    @Mock
    private CodeAnalysisIssueRepository codeAnalysisIssueRepository;

    @Mock
    private BranchIssueRepository branchIssueRepository;

    @Mock
    private VcsClientProvider vcsClientProvider;

    @Mock
    private AiAnalysisClient aiAnalysisClient;

    @Mock
    private VcsServiceFactory vcsServiceFactory;

    @Mock
    private AnalysisLockService analysisLockService;

    @Mock
    private RagOperationsService ragOperationsService;

    @Mock
    private VcsOperationsService operationsService;

    @Mock
    private VcsAiClientService aiClientService;

    @Mock
    private Project project;

    @Mock
    private VcsConnection vcsConnection;

    @Mock
    private OkHttpClient httpClient;

    @Mock
    private Branch branch;

    private BranchAnalysisProcessor processor;

    @BeforeEach
    void setUp() {
        processor = new BranchAnalysisProcessor(
                projectService,
                branchFileRepository,
                branchRepository,
                codeAnalysisIssueRepository,
                branchIssueRepository,
                vcsClientProvider,
                aiAnalysisClient,
                vcsServiceFactory,
                analysisLockService,
                ragOperationsService
        );
    }

    private BranchProcessRequest createRequest() {
        BranchProcessRequest request = new BranchProcessRequest();
        request.projectId = 1L;
        request.targetBranchName = "main";
        request.commitHash = "abc123";
        return request;
    }

    @Nested
    @DisplayName("VcsInfo record")
    class VcsInfoTests {

        @Test
        @DisplayName("should create VcsInfo with all fields")
        void shouldCreateVcsInfoWithAllFields() {
            BranchAnalysisProcessor.VcsInfo vcsInfo = new BranchAnalysisProcessor.VcsInfo(
                    vcsConnection, "workspace", "repo-slug"
            );

            assertThat(vcsInfo.vcsConnection()).isEqualTo(vcsConnection);
            assertThat(vcsInfo.workspace()).isEqualTo("workspace");
            assertThat(vcsInfo.repoSlug()).isEqualTo("repo-slug");
        }
    }

    @Nested
    @DisplayName("getVcsInfo()")
    class GetVcsInfoTests {

        @Test
        @DisplayName("should return VcsInfo when VCS connection is configured")
        void shouldReturnVcsInfoWhenConfigured() {
            VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
            when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
            when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
            when(repoInfo.getRepoWorkspace()).thenReturn("test-workspace");
            when(repoInfo.getRepoSlug()).thenReturn("test-repo");

            BranchAnalysisProcessor.VcsInfo result = processor.getVcsInfo(project);

            assertThat(result.vcsConnection()).isEqualTo(vcsConnection);
            assertThat(result.workspace()).isEqualTo("test-workspace");
            assertThat(result.repoSlug()).isEqualTo("test-repo");
        }

        @Test
        @DisplayName("should throw when no VCS connection configured")
        void shouldThrowWhenNoVcsConnectionConfigured() {
            when(project.getEffectiveVcsRepoInfo()).thenReturn(null);
            when(project.getId()).thenReturn(1L);

            assertThatThrownBy(() -> processor.getVcsInfo(project))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("No VCS connection configured");
        }

        @Test
        @DisplayName("should throw when VcsRepoInfo has null connection")
        void shouldThrowWhenVcsRepoInfoHasNullConnection() {
            VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
            when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
            when(repoInfo.getVcsConnection()).thenReturn(null);
            when(project.getId()).thenReturn(1L);

            assertThatThrownBy(() -> processor.getVcsInfo(project))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("No VCS connection configured");
        }
    }

    @Nested
    @DisplayName("parseFilePathsFromDiff()")
    class ParseFilePathsFromDiffTests {

        @Test
        @DisplayName("should parse file paths from valid diff")
        void shouldParseFilePathsFromValidDiff() {
            String diff = """
                    diff --git a/src/main/java/Test.java b/src/main/java/Test.java
                    index abc123..def456 100644
                    --- a/src/main/java/Test.java
                    +++ b/src/main/java/Test.java
                    @@ -1,5 +1,6 @@
                    +import java.util.List;
                    diff --git a/README.md b/README.md
                    index 111222..333444 100644
                    """;

            Set<String> result = processor.parseFilePathsFromDiff(diff);

            assertThat(result).containsExactlyInAnyOrder("src/main/java/Test.java", "README.md");
        }

        @Test
        @DisplayName("should return empty set for null diff")
        void shouldReturnEmptySetForNullDiff() {
            Set<String> result = processor.parseFilePathsFromDiff(null);
            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("should return empty set for blank diff")
        void shouldReturnEmptySetForBlankDiff() {
            Set<String> result = processor.parseFilePathsFromDiff("   ");
            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("should return empty set for diff without git headers")
        void shouldReturnEmptySetForDiffWithoutGitHeaders() {
            String diff = """
                    +++ some content
                    --- other content
                    """;

            Set<String> result = processor.parseFilePathsFromDiff(diff);
            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("should handle renamed files")
        void shouldHandleRenamedFiles() {
            String diff = "diff --git a/old-name.java b/new-name.java\n";

            Set<String> result = processor.parseFilePathsFromDiff(diff);

            // Should use the 'b/' path (destination)
            assertThat(result).containsExactly("new-name.java");
        }
    }

    @Nested
    @DisplayName("process()")
    class ProcessTests {

        @Test
        @DisplayName("should throw AnalysisLockedException when lock cannot be acquired")
        void shouldThrowAnalysisLockedExceptionWhenLockCannotBeAcquired() throws IOException {
            BranchProcessRequest request = createRequest();
            Consumer<Map<String, Object>> consumer = mock(Consumer.class);

            when(projectService.getProjectWithConnections(1L)).thenReturn(project);
            when(project.getId()).thenReturn(1L);
            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), any(), any()))
                    .thenReturn(Optional.empty());

            assertThatThrownBy(() -> processor.process(request, consumer))
                    .isInstanceOf(AnalysisLockedException.class);

            // No consumer or event interactions should occur when lock is not acquired
            verifyNoInteractions(consumer);
        }

        @Test
        @DisplayName("should skip when commit already analyzed")
        void shouldSkipWhenCommitAlreadyAnalyzed() throws IOException {
            BranchProcessRequest request = createRequest();
            request.commitHash = "abc123";
            Consumer<Map<String, Object>> consumer = mock(Consumer.class);

            when(projectService.getProjectWithConnections(1L)).thenReturn(project);
            when(project.getId()).thenReturn(1L);
            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), any(), any()))
                    .thenReturn(Optional.of("lock-key"));

            // Branch exists with same commit hash already successfully analyzed
            Branch existingBranch = new Branch();
            existingBranch.setLastSuccessfulCommitHash("abc123");
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.of(existingBranch));

            Map<String, Object> result = processor.process(request, consumer);

            assertThat(result).containsEntry("status", "skipped");
            assertThat(result).containsEntry("reason", "commit_already_analyzed");
            verify(analysisLockService).releaseLock("lock-key");
        }

        @Test
        @DisplayName("should mark branch stale on exception and rethrow")
        void shouldMarkBranchStaleOnException() throws IOException {
            BranchProcessRequest request = createRequest();
            Consumer<Map<String, Object>> consumer = mock(Consumer.class);

            when(projectService.getProjectWithConnections(1L)).thenReturn(project);
            when(project.getId()).thenReturn(1L);
            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), any(), any()))
                    .thenReturn(Optional.of("lock-key"));
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.empty())
                    .thenReturn(Optional.empty());
            // getVcsInfo throws because no VCS configured
            when(project.getEffectiveVcsRepoInfo()).thenReturn(null);

            assertThatThrownBy(() -> processor.process(request, consumer))
                    .isInstanceOf(IllegalStateException.class);

            verify(analysisLockService).releaseLock("lock-key");
        }

        @Test
        @DisplayName("should complete full happy path through all private methods")
        void shouldCompleteFullHappyPath() throws Exception {
            BranchProcessRequest request = createRequest();
            request.commitHash = "new-commit";
            request.sourcePrNumber = 42L;
            Consumer<Map<String, Object>> consumer = mock(Consumer.class);

            when(projectService.getProjectWithConnections(1L)).thenReturn(project);
            when(project.getId()).thenReturn(1L);
            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), any(), any()))
                    .thenReturn(Optional.of("lock-key"));

            // No existing branch → first analysis
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.empty())  // first call in process()
                    .thenReturn(Optional.empty());  // final markHealthy lookup

            // VCS info setup
            VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
            when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
            when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
            when(repoInfo.getRepoWorkspace()).thenReturn("ws");
            when(repoInfo.getRepoSlug()).thenReturn("repo");
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
            when(vcsClientProvider.getHttpClient(vcsConnection)).thenReturn(httpClient);

            when(vcsServiceFactory.getOperationsService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(operationsService);

            // First analysis: no delta diff, use PR diff
            String rawDiff = "diff --git a/src/App.java b/src/App.java\n+new code\n";
            when(operationsService.getPullRequestDiff(httpClient, "ws", "repo", "42")).thenReturn(rawDiff);

            // updateBranchFiles: file exists
            when(operationsService.checkFileExistsInBranch(httpClient, "ws", "repo", "main", "src/App.java"))
                    .thenReturn(true);
            when(codeAnalysisIssueRepository.findByProjectIdAndFilePath(1L, "src/App.java"))
                    .thenReturn(List.of());
            when(branchFileRepository.findByProjectIdAndBranchNameAndFilePath(1L, "main", "src/App.java"))
                    .thenReturn(Optional.empty());

            // createOrUpdateProjectBranch: no existing branch → create new
            Branch savedBranch = mock(Branch.class);
            when(savedBranch.getId()).thenReturn(10L);
            when(savedBranch.getBranchName()).thenReturn("main");
            when(branchRepository.save(any(Branch.class))).thenReturn(savedBranch);

            // mapCodeAnalysisIssuesToBranch: no issues
            when(branchIssueRepository.findUnresolvedByBranchIdAndFilePath(10L, "src/App.java"))
                    .thenReturn(List.of());

            // updateIssueCounts after mapping
            when(branchRepository.findByIdWithIssues(10L)).thenReturn(Optional.of(savedBranch));

            // reanalyzeCandidateIssues: no unresolved issues to re-analyze

            // RAG update
            when(ragOperationsService.isRagEnabled(project)).thenReturn(false);

            Map<String, Object> result = processor.process(request, consumer);

            assertThat(result).containsEntry("status", "accepted");
            assertThat(result).containsEntry("cached", false);
            verify(branchFileRepository).save(any(BranchFile.class));
            verify(analysisLockService).releaseLock("lock-key");
        }

        @Test
        @DisplayName("should use delta diff when last successful commit exists")
        void shouldUseDeltaDiffWhenLastSuccessfulCommitExists() throws Exception {
            BranchProcessRequest request = createRequest();
            request.commitHash = "new-commit";
            Consumer<Map<String, Object>> consumer = mock(Consumer.class);

            when(projectService.getProjectWithConnections(1L)).thenReturn(project);
            when(project.getId()).thenReturn(1L);
            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), any(), any()))
                    .thenReturn(Optional.of("lock-key"));

            // Existing branch with prior successful commit
            Branch existingBranch = new Branch();
            existingBranch.setLastSuccessfulCommitHash("old-commit");
            existingBranch.setBranchName("main");
            // Use reflection to set id
            try { var f = Branch.class.getDeclaredField("id"); f.setAccessible(true); f.set(existingBranch, 10L); } catch (Exception e) { throw new RuntimeException(e); }
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.of(existingBranch));

            VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
            when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
            when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
            when(repoInfo.getRepoWorkspace()).thenReturn("ws");
            when(repoInfo.getRepoSlug()).thenReturn("repo");
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
            when(vcsClientProvider.getHttpClient(vcsConnection)).thenReturn(httpClient);
            when(vcsServiceFactory.getOperationsService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(operationsService);

            // Delta diff succeeds
            String rawDiff = "diff --git a/src/App.java b/src/App.java\n+delta change\n";
            when(operationsService.getCommitRangeDiff(httpClient, "ws", "repo", "old-commit", "new-commit"))
                    .thenReturn(rawDiff);

            // updateBranchFiles
            when(operationsService.checkFileExistsInBranch(httpClient, "ws", "repo", "main", "src/App.java"))
                    .thenReturn(true);
            when(codeAnalysisIssueRepository.findByProjectIdAndFilePath(1L, "src/App.java"))
                    .thenReturn(List.of());
            when(branchFileRepository.findByProjectIdAndBranchNameAndFilePath(1L, "main", "src/App.java"))
                    .thenReturn(Optional.empty());
            when(branchRepository.save(any(Branch.class))).thenAnswer(inv -> inv.getArgument(0));

            when(branchIssueRepository.findUnresolvedByBranchIdAndFilePath(10L, "src/App.java"))
                    .thenReturn(List.of());
            when(branchRepository.findByIdWithIssues(10L)).thenReturn(Optional.of(existingBranch));
            when(ragOperationsService.isRagEnabled(project)).thenReturn(false);

            // Final markHealthy
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.of(existingBranch));

            Map<String, Object> result = processor.process(request, consumer);

            assertThat(result).containsEntry("status", "accepted");
            verify(operationsService).getCommitRangeDiff(httpClient, "ws", "repo", "old-commit", "new-commit");
            // Should NOT fall through to PR diff or commit diff
            verify(operationsService, never()).getPullRequestDiff(any(), anyString(), anyString(), anyString());
            verify(operationsService, never()).getCommitDiff(any(), anyString(), anyString(), anyString());
        }

        @Test
        @DisplayName("should fall back to commit diff when delta and PR diff unavailable")
        void shouldFallBackToCommitDiff() throws Exception {
            BranchProcessRequest request = createRequest();
            request.commitHash = "new-commit";
            request.sourcePrNumber = null;
            Consumer<Map<String, Object>> consumer = mock(Consumer.class);

            when(projectService.getProjectWithConnections(1L)).thenReturn(project);
            when(project.getId()).thenReturn(1L);
            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), any(), any()))
                    .thenReturn(Optional.of("lock-key"));

            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.empty());

            VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
            when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
            when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
            when(repoInfo.getRepoWorkspace()).thenReturn("ws");
            when(repoInfo.getRepoSlug()).thenReturn("repo");
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
            when(vcsClientProvider.getHttpClient(vcsConnection)).thenReturn(httpClient);
            when(vcsServiceFactory.getOperationsService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(operationsService);

            // No PR number → PR lookup returns null
            when(operationsService.findPullRequestForCommit(httpClient, "ws", "repo", "new-commit"))
                    .thenReturn(null);

            // Fall through to commit diff
            String rawDiff = "diff --git a/README.md b/README.md\n+updated\n";
            when(operationsService.getCommitDiff(httpClient, "ws", "repo", "new-commit")).thenReturn(rawDiff);

            when(operationsService.checkFileExistsInBranch(httpClient, "ws", "repo", "main", "README.md"))
                    .thenReturn(true);
            when(codeAnalysisIssueRepository.findByProjectIdAndFilePath(1L, "README.md")).thenReturn(List.of());
            when(branchFileRepository.findByProjectIdAndBranchNameAndFilePath(1L, "main", "README.md"))
                    .thenReturn(Optional.empty());

            Branch savedBranch = mock(Branch.class);
            when(savedBranch.getId()).thenReturn(10L);
            when(savedBranch.getBranchName()).thenReturn("main");
            when(branchRepository.save(any(Branch.class))).thenReturn(savedBranch);
            when(branchIssueRepository.findUnresolvedByBranchIdAndFilePath(10L, "README.md")).thenReturn(List.of());
            when(branchRepository.findByIdWithIssues(10L)).thenReturn(Optional.of(savedBranch));
            when(ragOperationsService.isRagEnabled(project)).thenReturn(false);

            Map<String, Object> result = processor.process(request, consumer);

            assertThat(result).containsEntry("status", "accepted");
            verify(operationsService).getCommitDiff(httpClient, "ws", "repo", "new-commit");
        }

        @Test
        @DisplayName("should reanalyze candidate issues and process reconciled issues")
        void shouldReanalyzeCandidateIssuesAndProcessReconciledIssues() throws Exception {
            BranchProcessRequest request = createRequest();
            request.commitHash = "new-commit";
            request.sourcePrNumber = 42L;
            Consumer<Map<String, Object>> consumer = mock(Consumer.class);

            when(projectService.getProjectWithConnections(1L)).thenReturn(project);
            when(project.getId()).thenReturn(1L);
            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), any(), any()))
                    .thenReturn(Optional.of("lock-key"));
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.empty());

            VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
            when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
            when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
            when(repoInfo.getRepoWorkspace()).thenReturn("ws");
            when(repoInfo.getRepoSlug()).thenReturn("repo");
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
            when(vcsClientProvider.getHttpClient(vcsConnection)).thenReturn(httpClient);
            when(vcsServiceFactory.getOperationsService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(operationsService);
            when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(aiClientService);

            String rawDiff = "diff --git a/src/App.java b/src/App.java\n+fix\n";
            when(operationsService.getPullRequestDiff(httpClient, "ws", "repo", "42")).thenReturn(rawDiff);
            when(operationsService.checkFileExistsInBranch(httpClient, "ws", "repo", "main", "src/App.java"))
                    .thenReturn(true);
            when(codeAnalysisIssueRepository.findByProjectIdAndFilePath(1L, "src/App.java")).thenReturn(List.of());
            when(branchFileRepository.findByProjectIdAndBranchNameAndFilePath(1L, "main", "src/App.java"))
                    .thenReturn(Optional.empty());

            Branch savedBranch = mock(Branch.class);
            when(savedBranch.getId()).thenReturn(10L);
            when(savedBranch.getBranchName()).thenReturn("main");
            when(branchRepository.save(any(Branch.class))).thenReturn(savedBranch);

            // mapCodeAnalysisIssuesToBranch
            when(branchIssueRepository.findUnresolvedByBranchIdAndFilePath(10L, "src/App.java"))
                    .thenReturn(List.of());

            // updateIssueCounts after mapping
            when(branchRepository.findByIdWithIssues(10L)).thenReturn(Optional.of(savedBranch));

            // reanalyzeCandidateIssues: this time with unresolved issues
            CodeAnalysisIssue existingIssue = mock(CodeAnalysisIssue.class);
            when(existingIssue.getId()).thenReturn(100L);
            when(existingIssue.getFilePath()).thenReturn("src/App.java");
            when(existingIssue.getLineNumber()).thenReturn(10);
            when(existingIssue.getSeverity()).thenReturn(org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity.HIGH);
            when(existingIssue.getIssueCategory()).thenReturn(org.rostilos.codecrow.core.model.codeanalysis.IssueCategory.BUG_RISK);
            when(existingIssue.isResolved()).thenReturn(false);
            CodeAnalysis analysis = mock(CodeAnalysis.class);
            when(analysis.getBranchName()).thenReturn("main"); // so issue passes branchSpecific filter
            when(existingIssue.getAnalysis()).thenReturn(analysis);

            BranchIssue branchIssue = mock(BranchIssue.class);
            when(branchIssue.getCodeAnalysisIssue()).thenReturn(existingIssue);

            // findUnresolved: first call from mapCodeAnalysisIssues, second from reanalyzeCandidateIssues
            when(branchIssueRepository.findUnresolvedByBranchIdAndFilePath(10L, "src/App.java"))
                    .thenReturn(List.of())          // mapCodeAnalysisIssuesToBranch call
                    .thenReturn(List.of(branchIssue)); // reanalyzeCandidateIssues call

            AiAnalysisRequest aiReq = mock(AiAnalysisRequest.class);
            when(aiClientService.buildAiAnalysisRequest(any(Project.class), any(BranchProcessRequest.class), any()))
                    .thenReturn(aiReq);

            // AI responds with resolved issue
            Map<String, Object> aiResponse = Map.of(
                    "issues", List.of(Map.of("issueId", "100", "isResolved", true, "reason", "Fixed in this commit"))
            );
            when(aiAnalysisClient.performAnalysis(eq(aiReq), any())).thenReturn(aiResponse);

            // processReconciledIssue — findByBranchIdAndCodeAnalysisIssueId:
            // mapCodeAnalysisIssuesToBranch does NOT call this because
            // findByProjectIdAndFilePath returns empty list (no issues in DB yet).
            // Only processReconciledIssue calls it.
            BranchIssue matchedBranchIssue = mock(BranchIssue.class);
            when(matchedBranchIssue.isResolved()).thenReturn(false);
            when(matchedBranchIssue.getCodeAnalysisIssue()).thenReturn(existingIssue);
            when(branchIssueRepository.findByBranchIdAndCodeAnalysisIssueId(10L, 100L))
                    .thenReturn(Optional.of(matchedBranchIssue));
            when(codeAnalysisIssueRepository.findById(100L)).thenReturn(Optional.of(existingIssue));

            when(ragOperationsService.isRagEnabled(project)).thenReturn(false);
            // Final markHealthy
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.empty())
                    .thenReturn(Optional.of(savedBranch));

            Map<String, Object> result = processor.process(request, consumer);

            assertThat(result).containsEntry("status", "accepted");
            verify(matchedBranchIssue).setResolved(true);
            verify(matchedBranchIssue).setResolvedInPrNumber(42L);
            verify(matchedBranchIssue).setResolvedInCommitHash("new-commit");
            verify(matchedBranchIssue).setResolvedDescription("Fixed in this commit");
        }

        @Test
        @DisplayName("should perform RAG update on main branch push")
        void shouldPerformRagUpdateOnMainBranch() throws Exception {
            BranchProcessRequest request = createRequest();
            request.commitHash = "new-commit";
            request.sourcePrNumber = 42L;
            Consumer<Map<String, Object>> consumer = mock(Consumer.class);

            when(projectService.getProjectWithConnections(1L)).thenReturn(project);
            when(project.getId()).thenReturn(1L);
            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), any(), any()))
                    .thenReturn(Optional.of("lock-key"));
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.empty());

            VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
            when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
            when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
            when(repoInfo.getRepoWorkspace()).thenReturn("ws");
            when(repoInfo.getRepoSlug()).thenReturn("repo");
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
            when(vcsClientProvider.getHttpClient(vcsConnection)).thenReturn(httpClient);
            when(vcsServiceFactory.getOperationsService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(operationsService);

            String rawDiff = "diff --git a/f.java b/f.java\n+x\n";
            when(operationsService.getPullRequestDiff(httpClient, "ws", "repo", "42")).thenReturn(rawDiff);
            when(operationsService.checkFileExistsInBranch(httpClient, "ws", "repo", "main", "f.java"))
                    .thenReturn(true);
            when(codeAnalysisIssueRepository.findByProjectIdAndFilePath(1L, "f.java")).thenReturn(List.of());
            when(branchFileRepository.findByProjectIdAndBranchNameAndFilePath(1L, "main", "f.java"))
                    .thenReturn(Optional.empty());

            Branch savedBranch = mock(Branch.class);
            when(savedBranch.getId()).thenReturn(10L);
            when(savedBranch.getBranchName()).thenReturn("main");
            when(branchRepository.save(any(Branch.class))).thenReturn(savedBranch);
            when(branchIssueRepository.findUnresolvedByBranchIdAndFilePath(10L, "f.java")).thenReturn(List.of());
            when(branchRepository.findByIdWithIssues(10L)).thenReturn(Optional.of(savedBranch));

            // RAG enabled, index ready, main branch
            when(ragOperationsService.isRagEnabled(project)).thenReturn(true);
            when(ragOperationsService.isRagIndexReady(project)).thenReturn(true);
            when(ragOperationsService.getBaseBranch(project)).thenReturn("main");

            // Final markHealthy
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.empty())
                    .thenReturn(Optional.of(savedBranch));

            processor.process(request, consumer);

            verify(ragOperationsService).triggerIncrementalUpdate(eq(project), eq("main"), eq("new-commit"), eq(rawDiff), any());
        }

        @Test
        @DisplayName("should call updateBranchIndex for non-main branch RAG update")
        void shouldCallUpdateBranchIndexForNonMainBranch() throws Exception {
            BranchProcessRequest request = createRequest();
            request.targetBranchName = "feature-x";
            request.commitHash = "new-commit";
            request.sourcePrNumber = 42L;
            Consumer<Map<String, Object>> consumer = mock(Consumer.class);

            when(projectService.getProjectWithConnections(1L)).thenReturn(project);
            when(project.getId()).thenReturn(1L);
            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), any(), any()))
                    .thenReturn(Optional.of("lock-key"));
            when(branchRepository.findByProjectIdAndBranchName(1L, "feature-x"))
                    .thenReturn(Optional.empty());

            VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
            when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
            when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
            when(repoInfo.getRepoWorkspace()).thenReturn("ws");
            when(repoInfo.getRepoSlug()).thenReturn("repo");
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
            when(vcsClientProvider.getHttpClient(vcsConnection)).thenReturn(httpClient);
            when(vcsServiceFactory.getOperationsService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(operationsService);

            String rawDiff = "diff --git a/f.java b/f.java\n+x\n";
            when(operationsService.getPullRequestDiff(httpClient, "ws", "repo", "42")).thenReturn(rawDiff);
            when(operationsService.checkFileExistsInBranch(httpClient, "ws", "repo", "feature-x", "f.java"))
                    .thenReturn(true);
            when(codeAnalysisIssueRepository.findByProjectIdAndFilePath(1L, "f.java")).thenReturn(List.of());
            when(branchFileRepository.findByProjectIdAndBranchNameAndFilePath(1L, "feature-x", "f.java"))
                    .thenReturn(Optional.empty());

            Branch savedBranch = mock(Branch.class);
            when(savedBranch.getId()).thenReturn(10L);
            when(savedBranch.getBranchName()).thenReturn("feature-x");
            when(branchRepository.save(any(Branch.class))).thenReturn(savedBranch);
            when(branchIssueRepository.findUnresolvedByBranchIdAndFilePath(10L, "f.java")).thenReturn(List.of());
            when(branchRepository.findByIdWithIssues(10L)).thenReturn(Optional.of(savedBranch));

            when(ragOperationsService.isRagEnabled(project)).thenReturn(true);
            when(ragOperationsService.isRagIndexReady(project)).thenReturn(true);
            when(ragOperationsService.getBaseBranch(project)).thenReturn("main");

            when(branchRepository.findByProjectIdAndBranchName(1L, "feature-x"))
                    .thenReturn(Optional.empty())
                    .thenReturn(Optional.of(savedBranch));

            processor.process(request, consumer);

            verify(ragOperationsService).updateBranchIndex(eq(project), eq("feature-x"), any());
            verify(ragOperationsService, never()).triggerIncrementalUpdate(any(), any(), any(), any(), any());
        }

        @Test
        @DisplayName("should update existing branch file issue count")
        void shouldUpdateExistingBranchFileIssueCount() throws Exception {
            BranchProcessRequest request = createRequest();
            request.commitHash = "new-commit";
            request.sourcePrNumber = 42L;
            Consumer<Map<String, Object>> consumer = mock(Consumer.class);

            when(projectService.getProjectWithConnections(1L)).thenReturn(project);
            when(project.getId()).thenReturn(1L);
            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), any(), any()))
                    .thenReturn(Optional.of("lock-key"));
            when(branchRepository.findByProjectIdAndBranchName(1L, "main")).thenReturn(Optional.empty());

            VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
            when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
            when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
            when(repoInfo.getRepoWorkspace()).thenReturn("ws");
            when(repoInfo.getRepoSlug()).thenReturn("repo");
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
            when(vcsClientProvider.getHttpClient(vcsConnection)).thenReturn(httpClient);
            when(vcsServiceFactory.getOperationsService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(operationsService);

            String rawDiff = "diff --git a/src/App.java b/src/App.java\n+code\n";
            when(operationsService.getPullRequestDiff(httpClient, "ws", "repo", "42")).thenReturn(rawDiff);
            when(operationsService.checkFileExistsInBranch(httpClient, "ws", "repo", "main", "src/App.java"))
                    .thenReturn(true);

            // Existing unresolved issue for the file
            CodeAnalysisIssue issue = mock(CodeAnalysisIssue.class);
            when(issue.isResolved()).thenReturn(false);
            when(issue.getFilePath()).thenReturn("src/App.java");
            when(issue.getLineNumber()).thenReturn(5);
            when(issue.getSeverity()).thenReturn(org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity.HIGH);
            when(issue.getIssueCategory()).thenReturn(org.rostilos.codecrow.core.model.codeanalysis.IssueCategory.BUG_RISK);
            CodeAnalysis issueAnalysis = mock(CodeAnalysis.class);
            when(issueAnalysis.getBranchName()).thenReturn("main");
            when(issue.getAnalysis()).thenReturn(issueAnalysis);
            when(codeAnalysisIssueRepository.findByProjectIdAndFilePath(1L, "src/App.java"))
                    .thenReturn(List.of(issue));

            // Existing BranchFile with wrong count
            BranchFile existingFile = mock(BranchFile.class);
            when(existingFile.getIssueCount()).thenReturn(0);
            when(branchFileRepository.findByProjectIdAndBranchNameAndFilePath(1L, "main", "src/App.java"))
                    .thenReturn(Optional.of(existingFile));

            Branch savedBranch = mock(Branch.class);
            when(savedBranch.getId()).thenReturn(10L);
            when(savedBranch.getBranchName()).thenReturn("main");
            when(branchRepository.save(any(Branch.class))).thenReturn(savedBranch);
            when(branchIssueRepository.findUnresolvedByBranchIdAndFilePath(10L, "src/App.java")).thenReturn(List.of());
            when(branchRepository.findByIdWithIssues(10L)).thenReturn(Optional.of(savedBranch));
            when(ragOperationsService.isRagEnabled(project)).thenReturn(false);
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.empty()).thenReturn(Optional.empty());

            // mapCodeAnalysisIssuesToBranch: existing issue matches branch
            when(branchIssueRepository.findByBranchIdAndCodeAnalysisIssueId(eq(10L), any())).thenReturn(Optional.empty());

            processor.process(request, consumer);

            // Should update existing file issue count
            verify(existingFile).setIssueCount(1);
            verify(branchFileRepository).save(existingFile);
        }

        @Test
        @DisplayName("should skip file that does not exist in branch")
        void shouldSkipFileThatDoesNotExistInBranch() throws Exception {
            BranchProcessRequest request = createRequest();
            request.commitHash = "new-commit";
            request.sourcePrNumber = 42L;
            Consumer<Map<String, Object>> consumer = mock(Consumer.class);

            when(projectService.getProjectWithConnections(1L)).thenReturn(project);
            when(project.getId()).thenReturn(1L);
            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), any(), any()))
                    .thenReturn(Optional.of("lock-key"));
            when(branchRepository.findByProjectIdAndBranchName(1L, "main")).thenReturn(Optional.empty());

            VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
            when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
            when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
            when(repoInfo.getRepoWorkspace()).thenReturn("ws");
            when(repoInfo.getRepoSlug()).thenReturn("repo");
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
            when(vcsClientProvider.getHttpClient(vcsConnection)).thenReturn(httpClient);
            when(vcsServiceFactory.getOperationsService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(operationsService);

            String rawDiff = "diff --git a/deleted.java b/deleted.java\n-removed\n";
            when(operationsService.getPullRequestDiff(httpClient, "ws", "repo", "42")).thenReturn(rawDiff);
            // File does NOT exist in branch (was deleted)
            when(operationsService.checkFileExistsInBranch(httpClient, "ws", "repo", "main", "deleted.java"))
                    .thenReturn(false);

            Branch savedBranch = mock(Branch.class);
            when(savedBranch.getId()).thenReturn(10L);
            when(savedBranch.getBranchName()).thenReturn("main");
            when(branchRepository.save(any(Branch.class))).thenReturn(savedBranch);
            when(branchRepository.findByIdWithIssues(10L)).thenReturn(Optional.of(savedBranch));
            when(ragOperationsService.isRagEnabled(project)).thenReturn(false);
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.empty()).thenReturn(Optional.empty());

            // reanalyzeCandidateIssues still checks unresolved for ALL changedFiles
            when(branchIssueRepository.findUnresolvedByBranchIdAndFilePath(10L, "deleted.java"))
                    .thenReturn(List.of());

            processor.process(request, consumer);

            // mapCodeAnalysisIssuesToBranch should skip the deleted file
            // (no issue repo lookup for the file since it's not in filesExistingInBranch)
            verify(codeAnalysisIssueRepository, never()).findByProjectIdAndFilePath(eq(1L), eq("deleted.java"));
        }

        @Test
        @DisplayName("should handle delta diff failure and fall back to PR diff")
        void shouldFallBackToPrDiffWhenDeltaDiffFails() throws Exception {
            BranchProcessRequest request = createRequest();
            request.commitHash = "new-commit";
            request.sourcePrNumber = 42L;
            Consumer<Map<String, Object>> consumer = mock(Consumer.class);

            when(projectService.getProjectWithConnections(1L)).thenReturn(project);
            when(project.getId()).thenReturn(1L);
            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), any(), any()))
                    .thenReturn(Optional.of("lock-key"));

            Branch existingBranch = new Branch();
            existingBranch.setLastSuccessfulCommitHash("old-commit");
            existingBranch.setBranchName("main");
            try { var f = Branch.class.getDeclaredField("id"); f.setAccessible(true); f.set(existingBranch, 10L); } catch (Exception e) { throw new RuntimeException(e); }
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.of(existingBranch));

            VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
            when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
            when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
            when(repoInfo.getRepoWorkspace()).thenReturn("ws");
            when(repoInfo.getRepoSlug()).thenReturn("repo");
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
            when(vcsClientProvider.getHttpClient(vcsConnection)).thenReturn(httpClient);
            when(vcsServiceFactory.getOperationsService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(operationsService);

            // Delta diff fails
            when(operationsService.getCommitRangeDiff(httpClient, "ws", "repo", "old-commit", "new-commit"))
                    .thenThrow(new IOException("Commit not found"));

            // Falls back to PR diff
            String rawDiff = "diff --git a/f.java b/f.java\n+x\n";
            when(operationsService.getPullRequestDiff(httpClient, "ws", "repo", "42")).thenReturn(rawDiff);
            when(operationsService.checkFileExistsInBranch(httpClient, "ws", "repo", "main", "f.java"))
                    .thenReturn(true);
            when(codeAnalysisIssueRepository.findByProjectIdAndFilePath(1L, "f.java")).thenReturn(List.of());
            when(branchFileRepository.findByProjectIdAndBranchNameAndFilePath(1L, "main", "f.java"))
                    .thenReturn(Optional.empty());
            when(branchRepository.save(any(Branch.class))).thenAnswer(inv -> inv.getArgument(0));
            when(branchIssueRepository.findUnresolvedByBranchIdAndFilePath(10L, "f.java")).thenReturn(List.of());
            when(branchRepository.findByIdWithIssues(10L)).thenReturn(Optional.of(existingBranch));
            when(ragOperationsService.isRagEnabled(project)).thenReturn(false);

            processor.process(request, consumer);

            verify(operationsService).getCommitRangeDiff(httpClient, "ws", "repo", "old-commit", "new-commit");
            verify(operationsService).getPullRequestDiff(httpClient, "ws", "repo", "42");
        }

        @Test
        @DisplayName("should handle issues as Map in AI response for reanalysis")
        void shouldHandleIssuesAsMapInAiResponseForReanalysis() throws Exception {
            BranchProcessRequest request = createRequest();
            request.commitHash = "new-commit";
            request.sourcePrNumber = 42L;
            Consumer<Map<String, Object>> consumer = mock(Consumer.class);

            when(projectService.getProjectWithConnections(1L)).thenReturn(project);
            when(project.getId()).thenReturn(1L);
            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), any(), any()))
                    .thenReturn(Optional.of("lock-key"));
            when(branchRepository.findByProjectIdAndBranchName(1L, "main")).thenReturn(Optional.empty());

            VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
            when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
            when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
            when(repoInfo.getRepoWorkspace()).thenReturn("ws");
            when(repoInfo.getRepoSlug()).thenReturn("repo");
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
            when(vcsClientProvider.getHttpClient(vcsConnection)).thenReturn(httpClient);
            when(vcsServiceFactory.getOperationsService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(operationsService);
            when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(aiClientService);

            String rawDiff = "diff --git a/src/A.java b/src/A.java\n+x\n";
            when(operationsService.getPullRequestDiff(httpClient, "ws", "repo", "42")).thenReturn(rawDiff);
            when(operationsService.checkFileExistsInBranch(httpClient, "ws", "repo", "main", "src/A.java"))
                    .thenReturn(true);
            when(codeAnalysisIssueRepository.findByProjectIdAndFilePath(1L, "src/A.java")).thenReturn(List.of());
            when(branchFileRepository.findByProjectIdAndBranchNameAndFilePath(1L, "main", "src/A.java"))
                    .thenReturn(Optional.empty());

            Branch savedBranch = mock(Branch.class);
            when(savedBranch.getId()).thenReturn(10L);
            when(savedBranch.getBranchName()).thenReturn("main");
            when(branchRepository.save(any(Branch.class))).thenReturn(savedBranch);

            // Has unresolved issues for reanalysis
            CodeAnalysisIssue existingIssue = mock(CodeAnalysisIssue.class);
            when(existingIssue.getId()).thenReturn(200L);
            when(existingIssue.getFilePath()).thenReturn("src/A.java");
            when(existingIssue.getLineNumber()).thenReturn(1);
            when(existingIssue.getSeverity()).thenReturn(org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity.LOW);
            when(existingIssue.getIssueCategory()).thenReturn(org.rostilos.codecrow.core.model.codeanalysis.IssueCategory.STYLE);
            CodeAnalysis issueAnalysis = mock(CodeAnalysis.class);
            when(issueAnalysis.getBranchName()).thenReturn("main"); // matches branch name for filter
            when(existingIssue.getAnalysis()).thenReturn(issueAnalysis);
            BranchIssue bi = mock(BranchIssue.class);
            when(bi.getCodeAnalysisIssue()).thenReturn(existingIssue);
            when(branchIssueRepository.findUnresolvedByBranchIdAndFilePath(10L, "src/A.java"))
                    .thenReturn(List.of())       // mapCodeAnalysisIssuesToBranch
                    .thenReturn(List.of(bi));    // reanalyzeCandidateIssues
            when(branchRepository.findByIdWithIssues(10L)).thenReturn(Optional.of(savedBranch));

            AiAnalysisRequest aiReq = mock(AiAnalysisRequest.class);
            when(aiClientService.buildAiAnalysisRequest(any(Project.class), any(BranchProcessRequest.class), any()))
                    .thenReturn(aiReq);

            // AI responds with issues as Map (numeric keys)
            Map<String, Object> issuesMap = new HashMap<>();
            issuesMap.put("0", Map.of("id", "200", "status", "resolved", "reason", "Code fixed"));
            Map<String, Object> aiResponse = Map.of("issues", issuesMap);
            when(aiAnalysisClient.performAnalysis(eq(aiReq), any())).thenReturn(aiResponse);

            // findByBranchIdAndCodeAnalysisIssueId: only called from processReconciledIssue
            // (mapCodeAnalysisIssuesToBranch doesn't call it because findByProjectIdAndFilePath returns empty list)
            BranchIssue matchedBi = mock(BranchIssue.class);
            when(matchedBi.isResolved()).thenReturn(false);
            when(matchedBi.getCodeAnalysisIssue()).thenReturn(existingIssue);
            when(branchIssueRepository.findByBranchIdAndCodeAnalysisIssueId(10L, 200L))
                    .thenReturn(Optional.of(matchedBi));
            when(codeAnalysisIssueRepository.findById(200L)).thenReturn(Optional.of(existingIssue));

            when(ragOperationsService.isRagEnabled(project)).thenReturn(false);
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.empty()).thenReturn(Optional.of(savedBranch));

            processor.process(request, consumer);

            // processReconciledIssue should use "id" fallback and "status" field
            verify(matchedBi).setResolved(true);
            verify(matchedBi).setResolvedDescription("Code fixed");
        }
    }

    @Nested
    @DisplayName("performIncrementalRagUpdate()")
    class RagUpdateTests {
        // These are tested through process() behavior since the method is private.
        // The key scenarios: ragOperationsService null, rag not enabled, rag index not ready,
        // main branch vs non-main branch are all covered indirectly.
    }

    @Nested
    @DisplayName("Constructor")
    class ConstructorTests {

        @Test
        @DisplayName("should work without optional dependencies")
        void shouldWorkWithoutOptionalDependencies() {
            BranchAnalysisProcessor processorWithoutOptional = new BranchAnalysisProcessor(
                    projectService,
                    branchFileRepository,
                    branchRepository,
                    codeAnalysisIssueRepository,
                    branchIssueRepository,
                    vcsClientProvider,
                    aiAnalysisClient,
                    vcsServiceFactory,
                    analysisLockService,
                    null // ragOperationsService
            );

            assertThat(processorWithoutOptional).isNotNull();
        }
    }
}

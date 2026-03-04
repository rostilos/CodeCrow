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
import org.rostilos.codecrow.commitgraph.dag.CommitRangeContext;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException;
import org.rostilos.codecrow.analysisengine.processor.VcsRepoInfoImpl;
import org.rostilos.codecrow.analysisengine.service.branch.BranchDiffFetcher;
import org.rostilos.codecrow.analysisengine.service.branch.BranchFileOperationsService;
import org.rostilos.codecrow.analysisengine.service.branch.BranchHealthService;
import org.rostilos.codecrow.analysisengine.service.branch.BranchIssueMappingService;
import org.rostilos.codecrow.analysisengine.service.branch.BranchIssueReconciliationService;
import org.rostilos.codecrow.commitgraph.service.BranchCommitService;
import org.rostilos.codecrow.commitgraph.service.AnalyzedCommitService;
import org.rostilos.codecrow.analysisengine.service.AstScopeEnricher;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.ProjectValidationService;
import org.rostilos.codecrow.analysisengine.service.PullRequestService;
import org.rostilos.codecrow.commitgraph.service.CommitCoverageService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.analysisengine.util.DiffParsingUtils;
import org.rostilos.codecrow.analysisengine.util.ProjectVcsInfoRetriever;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
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
    private ProjectValidationService projectService;

    @Mock
    private BranchRepository branchRepository;

    @Mock
    private VcsClientProvider vcsClientProvider;

    @Mock
    private VcsServiceFactory vcsServiceFactory;

    @Mock
    private AnalysisLockService analysisLockService;

    @Mock
    private BranchFileOperationsService branchFileOperationsService;

    @Mock
    private BranchHealthService branchHealthService;

    @Mock
    private BranchDiffFetcher branchDiffFetcher;

    @Mock
    private BranchCommitService branchCommitService;

    @Mock
    private AnalyzedCommitService analyzedCommitService;

    @Mock
    private BranchIssueMappingService branchIssueMappingService;

    @Mock
    private BranchIssueReconciliationService branchIssueReconciliationService;

    @Mock
    private CommitCoverageService commitCoverageService;

    @Mock
    private CodeAnalysisService codeAnalysisService;

    @Mock
    private AiAnalysisClient aiAnalysisClient;

    @Mock
    private PullRequestService pullRequestService;

    @Mock
    private AstScopeEnricher astScopeEnricher;

    @Mock
    private RagOperationsService ragOperationsService;

    @Mock
    private VcsOperationsService operationsService;

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
                branchRepository,
                vcsClientProvider,
                vcsServiceFactory,
                analysisLockService,
                branchFileOperationsService,
                branchIssueMappingService,
                branchIssueReconciliationService,
                branchHealthService,
                branchDiffFetcher,
                branchCommitService,
                analyzedCommitService,
                commitCoverageService,
                codeAnalysisService,
                aiAnalysisClient,
                pullRequestService,
                astScopeEnricher,
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
    @DisplayName("VcsRepoInfoImpl record")
    class VcsRepoInfoImplTests {

        @Test
        @DisplayName("should create VcsRepoInfoImpl with all fields")
        void shouldCreateVcsRepoInfoImplWithAllFields() {
            VcsRepoInfoImpl vcsInfo = new VcsRepoInfoImpl(
                    vcsConnection, "workspace", "repo-slug"
            );

            assertThat(vcsInfo.vcsConnection()).isEqualTo(vcsConnection);
            assertThat(vcsInfo.workspace()).isEqualTo("workspace");
            assertThat(vcsInfo.repoSlug()).isEqualTo("repo-slug");
        }
    }

    @Nested
    @DisplayName("ProjectVcsInfoRetriever.getVcsInfo()")
    class GetVcsInfoTests {

        @Test
        @DisplayName("should return VcsRepoInfoImpl when VCS connection is configured")
        void shouldReturnVcsInfoWhenConfigured() {
            VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
            when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
            when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
            when(repoInfo.getRepoWorkspace()).thenReturn("test-workspace");
            when(repoInfo.getRepoSlug()).thenReturn("test-repo");

            VcsRepoInfoImpl result = ProjectVcsInfoRetriever.getVcsInfo(project);

            assertThat(result.vcsConnection()).isEqualTo(vcsConnection);
            assertThat(result.workspace()).isEqualTo("test-workspace");
            assertThat(result.repoSlug()).isEqualTo("test-repo");
        }

        @Test
        @DisplayName("should throw when no VCS connection configured")
        void shouldThrowWhenNoVcsConnectionConfigured() {
            when(project.getEffectiveVcsRepoInfo()).thenReturn(null);
            when(project.getId()).thenReturn(1L);

            assertThatThrownBy(() -> ProjectVcsInfoRetriever.getVcsInfo(project))
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

            assertThatThrownBy(() -> ProjectVcsInfoRetriever.getVcsInfo(project))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("No VCS connection configured");
        }
    }

    @Nested
    @DisplayName("DiffParsingUtils.parseFilePathsFromDiff()")
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

            Set<String> result = DiffParsingUtils.parseFilePathsFromDiff(diff);

            assertThat(result).containsExactlyInAnyOrder("src/main/java/Test.java", "README.md");
        }

        @Test
        @DisplayName("should return empty set for null diff")
        void shouldReturnEmptySetForNullDiff() {
            Set<String> result = DiffParsingUtils.parseFilePathsFromDiff(null);
            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("should return empty set for blank diff")
        void shouldReturnEmptySetForBlankDiff() {
            Set<String> result = DiffParsingUtils.parseFilePathsFromDiff("   ");
            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("should return empty set for diff without git headers")
        void shouldReturnEmptySetForDiffWithoutGitHeaders() {
            String diff = """
                    +++ some content
                    --- other content
                    """;

            Set<String> result = DiffParsingUtils.parseFilePathsFromDiff(diff);
            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("should handle renamed files")
        void shouldHandleRenamedFiles() {
            String diff = "diff --git a/old-name.java b/new-name.java\n";

            Set<String> result = DiffParsingUtils.parseFilePathsFromDiff(diff);

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

            // matchCache refreshes snapshots — need branchFileOperationsService stubs
            when(branchFileOperationsService.getBranchFilePaths(1L, "main"))
                    .thenReturn(Collections.emptySet());

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
        @DisplayName("should complete full happy path delegating to support services")
        void shouldCompleteFullHappyPath() throws Exception {
            BranchProcessRequest request = createRequest();
            request.commitHash = "new-commit";
            request.sourcePrNumber = 42L;
            Consumer<Map<String, Object>> consumer = mock(Consumer.class);

            when(projectService.getProjectWithConnections(1L)).thenReturn(project);
            when(project.getId()).thenReturn(1L);
            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), any(), any()))
                    .thenReturn(Optional.of("lock-key"));

            // No existing branch -> first analysis
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.empty())  // first call in process()
                    .thenReturn(Optional.empty())  // branchForVerify
                    .thenReturn(Optional.empty());  // markHealthy lookup

            // VCS info setup
            VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
            when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
            when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
            when(repoInfo.getRepoWorkspace()).thenReturn("ws");
            when(repoInfo.getRepoSlug()).thenReturn("repo");
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
            when(vcsClientProvider.getHttpClient(vcsConnection)).thenReturn(httpClient);
            when(vcsServiceFactory.getOperationsService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(operationsService);

            // DAG sync
            when(branchCommitService.resolveCommitRange(any(), any(), any(), any()))
                    .thenReturn(new CommitRangeContext(Collections.emptyList(), null, false));

            // Diff fetcher returns the raw diff
            String rawDiff = "diff --git a/src/App.java b/src/App.java\n+new code\n";
            when(branchDiffFetcher.fetchDiff(any(), any(), any(), any(), any(), any(), any(), any()))
                    .thenReturn(rawDiff);

            // Support services return values
            Map<String, String> archiveContents = Map.of("src/App.java", "file content");
            when(branchFileOperationsService.downloadBranchArchive(any(), eq("new-commit"), anySet()))
                    .thenReturn(archiveContents);
            when(branchFileOperationsService.updateBranchFiles(anySet(), eq(project), eq("main"), eq(archiveContents)))
                    .thenReturn(Set.of("src/App.java"));

            Branch savedBranch = mock(Branch.class);
            when(savedBranch.getId()).thenReturn(10L);
            when(savedBranch.getBranchName()).thenReturn("main");
            when(branchFileOperationsService.createOrUpdateProjectBranch(eq(project), eq(request), any()))
                    .thenReturn(savedBranch);

            // refreshAndSaveIssueCounts
            when(branchRepository.findByIdWithIssues(10L)).thenReturn(Optional.of(savedBranch));
            when(branchRepository.save(any(Branch.class))).thenReturn(savedBranch);

            // RAG update
            when(ragOperationsService.isRagEnabled(project)).thenReturn(false);

            Map<String, Object> result = processor.process(request, consumer);

            assertThat(result).containsEntry("status", "accepted");
            assertThat(result).containsEntry("cached", false);

            // Verify orchestration calls to support services
            verify(branchFileOperationsService).downloadBranchArchive(any(), eq("new-commit"), anySet());
            verify(branchFileOperationsService).updateBranchFiles(anySet(), eq(project), eq("main"), eq(archiveContents));
            verify(branchFileOperationsService).createOrUpdateProjectBranch(eq(project), eq(request), any());
            verify(branchIssueMappingService).mapCodeAnalysisIssuesToBranch(anySet(), anySet(), eq(savedBranch), eq(project));
            verify(branchIssueReconciliationService).reconcileIssueLineNumbers(eq(rawDiff), anySet(), eq(savedBranch));
            verify(branchIssueReconciliationService).reanalyzeCandidateIssues(
                    anySet(), anySet(), eq(savedBranch), eq(project), eq(request), eq(consumer), eq(archiveContents), eq(rawDiff));
            verify(branchFileOperationsService).updateFileSnapshotsForBranch(anySet(), eq(project), eq(request), eq(archiveContents));
            verify(branchIssueReconciliationService).verifyIssueLineNumbersWithSnippets(anySet(), eq(project), any());
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

            // DAG sync
            when(branchCommitService.resolveCommitRange(any(), any(), any(), any()))
                    .thenReturn(new CommitRangeContext(Collections.emptyList(), null, false));

            // Diff fetcher returns delta diff
            String rawDiff = "diff --git a/src/App.java b/src/App.java\n+delta change\n";
            when(branchDiffFetcher.fetchDiff(any(), any(), any(), any(), any(), any(), any(), any()))
                    .thenReturn(rawDiff);

            // Support services
            Map<String, String> archiveContents = Map.of("src/App.java", "content");
            when(branchFileOperationsService.downloadBranchArchive(any(), eq("new-commit"), anySet()))
                    .thenReturn(archiveContents);
            when(branchFileOperationsService.updateBranchFiles(anySet(), eq(project), eq("main"), eq(archiveContents)))
                    .thenReturn(Set.of("src/App.java"));
            when(branchFileOperationsService.createOrUpdateProjectBranch(eq(project), eq(request), any()))
                    .thenReturn(existingBranch);
            when(branchRepository.findByIdWithIssues(10L)).thenReturn(Optional.of(existingBranch));
            when(branchRepository.save(any(Branch.class))).thenAnswer(inv -> inv.getArgument(0));
            when(ragOperationsService.isRagEnabled(project)).thenReturn(false);

            Map<String, Object> result = processor.process(request, consumer);

            assertThat(result).containsEntry("status", "accepted");
            verify(branchDiffFetcher).fetchDiff(any(), any(), any(), any(), any(), any(), any(), any());
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

            // DAG sync
            when(branchCommitService.resolveCommitRange(any(), any(), any(), any()))
                    .thenReturn(new CommitRangeContext(Collections.emptyList(), null, false));

            // Diff fetcher returns commit diff
            String rawDiff = "diff --git a/README.md b/README.md\n+updated\n";
            when(branchDiffFetcher.fetchDiff(any(), any(), any(), any(), any(), any(), any(), any()))
                    .thenReturn(rawDiff);

            // Support services
            Map<String, String> archiveContents = Map.of("README.md", "content");
            when(branchFileOperationsService.downloadBranchArchive(any(), eq("new-commit"), anySet()))
                    .thenReturn(archiveContents);
            when(branchFileOperationsService.updateBranchFiles(anySet(), eq(project), eq("main"), eq(archiveContents)))
                    .thenReturn(Set.of("README.md"));

            Branch savedBranch = mock(Branch.class);
            when(savedBranch.getId()).thenReturn(10L);
            when(savedBranch.getBranchName()).thenReturn("main");
            when(branchFileOperationsService.createOrUpdateProjectBranch(eq(project), eq(request), any()))
                    .thenReturn(savedBranch);
            when(branchRepository.findByIdWithIssues(10L)).thenReturn(Optional.of(savedBranch));
            when(branchRepository.save(any(Branch.class))).thenReturn(savedBranch);
            when(ragOperationsService.isRagEnabled(project)).thenReturn(false);

            Map<String, Object> result = processor.process(request, consumer);

            assertThat(result).containsEntry("status", "accepted");
            verify(branchDiffFetcher).fetchDiff(any(), any(), any(), any(), any(), any(), any(), any());
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

            // DAG sync
            when(branchCommitService.resolveCommitRange(any(), any(), any(), any()))
                    .thenReturn(new CommitRangeContext(Collections.emptyList(), null, false));

            String rawDiff = "diff --git a/f.java b/f.java\n+x\n";
            when(branchDiffFetcher.fetchDiff(any(), any(), any(), any(), any(), any(), any(), any()))
                    .thenReturn(rawDiff);

            Map<String, String> archiveContents = Map.of("f.java", "content");
            when(branchFileOperationsService.downloadBranchArchive(any(), eq("new-commit"), anySet()))
                    .thenReturn(archiveContents);
            when(branchFileOperationsService.updateBranchFiles(anySet(), eq(project), eq("main"), eq(archiveContents)))
                    .thenReturn(Set.of("f.java"));

            Branch savedBranch = mock(Branch.class);
            when(savedBranch.getId()).thenReturn(10L);
            when(savedBranch.getBranchName()).thenReturn("main");
            when(branchFileOperationsService.createOrUpdateProjectBranch(eq(project), eq(request), any()))
                    .thenReturn(savedBranch);
            when(branchRepository.findByIdWithIssues(10L)).thenReturn(Optional.of(savedBranch));
            when(branchRepository.save(any(Branch.class))).thenReturn(savedBranch);

            // RAG enabled, index ready, main branch, pipeline healthy
            when(ragOperationsService.isRagEnabled(project)).thenReturn(true);
            when(ragOperationsService.isRagIndexReady(project)).thenReturn(true);
            when(ragOperationsService.getBaseBranch(project)).thenReturn("main");
            when(ragOperationsService.isRagPipelineHealthy()).thenReturn(true);

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

            // DAG sync
            when(branchCommitService.resolveCommitRange(any(), any(), any(), any()))
                    .thenReturn(new CommitRangeContext(Collections.emptyList(), null, false));

            String rawDiff = "diff --git a/f.java b/f.java\n+x\n";
            when(branchDiffFetcher.fetchDiff(any(), any(), any(), any(), any(), any(), any(), any()))
                    .thenReturn(rawDiff);

            Map<String, String> archiveContents = Map.of("f.java", "content");
            when(branchFileOperationsService.downloadBranchArchive(any(), eq("new-commit"), anySet()))
                    .thenReturn(archiveContents);
            when(branchFileOperationsService.updateBranchFiles(anySet(), eq(project), eq("feature-x"), eq(archiveContents)))
                    .thenReturn(Set.of("f.java"));

            Branch savedBranch = mock(Branch.class);
            when(savedBranch.getId()).thenReturn(10L);
            when(savedBranch.getBranchName()).thenReturn("feature-x");
            when(branchFileOperationsService.createOrUpdateProjectBranch(eq(project), eq(request), any()))
                    .thenReturn(savedBranch);
            when(branchRepository.findByIdWithIssues(10L)).thenReturn(Optional.of(savedBranch));
            when(branchRepository.save(any(Branch.class))).thenReturn(savedBranch);

            when(ragOperationsService.isRagEnabled(project)).thenReturn(true);
            when(ragOperationsService.isRagIndexReady(project)).thenReturn(true);
            when(ragOperationsService.getBaseBranch(project)).thenReturn("main");
            when(ragOperationsService.isRagPipelineHealthy()).thenReturn(true);

            when(branchRepository.findByProjectIdAndBranchName(1L, "feature-x"))
                    .thenReturn(Optional.empty())
                    .thenReturn(Optional.of(savedBranch));

            processor.process(request, consumer);

            verify(ragOperationsService).updateBranchIndex(eq(project), eq("feature-x"), any());
            verify(ragOperationsService, never()).triggerIncrementalUpdate(any(), any(), any(), any(), any());
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

            // DAG sync
            when(branchCommitService.resolveCommitRange(any(), any(), any(), any()))
                    .thenReturn(new CommitRangeContext(Collections.emptyList(), null, false));

            // Diff fetcher returns diff (handles fallback internally)
            String rawDiff = "diff --git a/f.java b/f.java\n+x\n";
            when(branchDiffFetcher.fetchDiff(any(), any(), any(), any(), any(), any(), any(), any()))
                    .thenReturn(rawDiff);

            Map<String, String> archiveContents = Map.of("f.java", "content");
            when(branchFileOperationsService.downloadBranchArchive(any(), eq("new-commit"), anySet()))
                    .thenReturn(archiveContents);
            when(branchFileOperationsService.updateBranchFiles(anySet(), eq(project), eq("main"), eq(archiveContents)))
                    .thenReturn(Set.of("f.java"));
            when(branchFileOperationsService.createOrUpdateProjectBranch(eq(project), eq(request), any()))
                    .thenReturn(existingBranch);
            when(branchRepository.findByIdWithIssues(10L)).thenReturn(Optional.of(existingBranch));
            when(branchRepository.save(any(Branch.class))).thenAnswer(inv -> inv.getArgument(0));
            when(ragOperationsService.isRagEnabled(project)).thenReturn(false);

            processor.process(request, consumer);

            verify(branchDiffFetcher).fetchDiff(any(), any(), any(), any(), any(), any(), any(), any());
        }
    }

    @Nested
    @DisplayName("fullReconcile()")
    class FullReconcileTests {

        @Test
        @DisplayName("should throw when branch not found")
        void shouldThrowWhenBranchNotFound() throws IOException {
            Consumer<Map<String, Object>> consumer = mock(Consumer.class);

            when(projectService.getProjectWithConnections(1L)).thenReturn(project);
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.empty());

            assertThatThrownBy(() -> processor.fullReconcile(1L, "main", consumer))
                    .isInstanceOf(IllegalArgumentException.class)
                    .hasMessageContaining("Branch not found");
        }

        @Test
        @DisplayName("should complete when no unresolved issues")
        void shouldCompleteWhenNoUnresolvedIssues() throws IOException {
            Consumer<Map<String, Object>> consumer = mock(Consumer.class);

            when(projectService.getProjectWithConnections(1L)).thenReturn(project);
            when(project.getId()).thenReturn(1L);

            Branch existingBranch = mock(Branch.class);
            when(existingBranch.getId()).thenReturn(10L);
            when(existingBranch.getLastSuccessfulCommitHash()).thenReturn("abc123");
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.of(existingBranch));
            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), any(), any()))
                    .thenReturn(Optional.of("lock-key"));

            // No unresolved issues
            Branch branchWithIssues = mock(Branch.class);
            when(branchWithIssues.getIssues()).thenReturn(Collections.emptyList());
            when(branchRepository.findByIdWithIssues(10L)).thenReturn(Optional.of(branchWithIssues));

            Map<String, Object> result = processor.fullReconcile(1L, "main", consumer);

            assertThat(result).containsEntry("status", "completed");
            assertThat(result).containsEntry("totalIssues", 0);
            verify(analysisLockService).releaseLock("lock-key");
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
                    branchRepository,
                    vcsClientProvider,
                    vcsServiceFactory,
                    analysisLockService,
                    branchFileOperationsService,
                    branchIssueMappingService,
                    branchIssueReconciliationService,
                    branchHealthService,
                    branchDiffFetcher,
                    branchCommitService,
                    analyzedCommitService,
                    commitCoverageService,
                    codeAnalysisService,
                    aiAnalysisClient,
                    pullRequestService,
                    null, // astScopeEnricher
                    null // ragOperationsService
            );

            assertThat(processorWithoutOptional).isNotNull();
        }
    }
}

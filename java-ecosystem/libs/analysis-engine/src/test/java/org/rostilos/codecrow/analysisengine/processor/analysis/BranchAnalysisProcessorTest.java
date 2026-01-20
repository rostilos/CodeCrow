package org.rostilos.codecrow.analysisengine.processor.analysis;

import okhttp3.OkHttpClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.ProjectService;
import org.rostilos.codecrow.analysisengine.service.rag.RagOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchFileRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.springframework.context.ApplicationEventPublisher;

import java.io.IOException;
import java.util.*;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
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
    private ApplicationEventPublisher eventPublisher;

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
                ragOperationsService,
                eventPublisher
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

        @Test
        @DisplayName("should handle files with spaces in path")
        void shouldHandleFilesWithSpacesInPath() {
            String diff = "diff --git a/path with spaces/file.java b/path with spaces/file.java\n";

            Set<String> result = processor.parseFilePathsFromDiff(diff);

            assertThat(result).containsExactly("path with spaces/file.java");
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
            when(project.getName()).thenReturn("Test Project");
            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), any(), any()))
                    .thenReturn(Optional.empty());

            assertThatThrownBy(() -> processor.process(request, consumer))
                    .isInstanceOf(AnalysisLockedException.class);

            verify(eventPublisher, times(2)).publishEvent(any());
        }

        @Test
        @DisplayName("should successfully process branch analysis")
        void shouldSuccessfullyProcessBranchAnalysis() throws IOException {
            BranchProcessRequest request = createRequest();
            Consumer<Map<String, Object>> consumer = mock(Consumer.class);

            VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
            when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
            when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
            when(repoInfo.getRepoWorkspace()).thenReturn("workspace");
            when(repoInfo.getRepoSlug()).thenReturn("repo");
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

            when(projectService.getProjectWithConnections(1L)).thenReturn(project);
            when(project.getId()).thenReturn(1L);
            when(project.getName()).thenReturn("Test Project");
            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), any(), any()))
                    .thenReturn(Optional.of("lock-key"));

            when(vcsClientProvider.getHttpClient(vcsConnection)).thenReturn(httpClient);
            when(vcsServiceFactory.getOperationsService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(operationsService);

            String diff = "diff --git a/file.java b/file.java\nindex abc..def";
            when(operationsService.getCommitDiff(any(), anyString(), anyString(), anyString())).thenReturn(diff);

            when(branchRepository.findByProjectIdAndBranchName(any(), anyString())).thenReturn(Optional.of(branch));
            when(branchRepository.findByIdWithIssues(any())).thenReturn(Optional.of(branch));
            when(branchRepository.save(any())).thenReturn(branch);

            Map<String, Object> result = processor.process(request, consumer);

            assertThat(result).containsEntry("status", "accepted");
            assertThat(result).containsEntry("branch", "main");
            verify(analysisLockService).releaseLock("lock-key");
        }

        @Test
        @DisplayName("should release lock even when exception occurs")
        void shouldReleaseLockEvenWhenExceptionOccurs() throws IOException {
            BranchProcessRequest request = createRequest();
            Consumer<Map<String, Object>> consumer = mock(Consumer.class);

            VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
            when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
            when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
            when(repoInfo.getRepoWorkspace()).thenReturn("workspace");
            when(repoInfo.getRepoSlug()).thenReturn("repo");
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

            when(projectService.getProjectWithConnections(1L)).thenReturn(project);
            when(project.getId()).thenReturn(1L);
            when(project.getName()).thenReturn("Test Project");
            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), any(), any()))
                    .thenReturn(Optional.of("lock-key"));

            when(vcsClientProvider.getHttpClient(vcsConnection)).thenReturn(httpClient);
            when(vcsServiceFactory.getOperationsService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(operationsService);
            when(operationsService.getCommitDiff(any(), anyString(), anyString(), anyString()))
                    .thenThrow(new IOException("Network error"));

            assertThatThrownBy(() -> processor.process(request, consumer))
                    .isInstanceOf(IOException.class)
                    .hasMessageContaining("Network error");

            verify(analysisLockService).releaseLock("lock-key");
        }

        @Test
        @DisplayName("should lookup PR from commit when sourcePrNumber not set")
        void shouldLookupPRFromCommitWhenSourcePrNumberNotSet() throws IOException {
            BranchProcessRequest request = createRequest();
            Consumer<Map<String, Object>> consumer = mock(Consumer.class);

            VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
            when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
            when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
            when(repoInfo.getRepoWorkspace()).thenReturn("workspace");
            when(repoInfo.getRepoSlug()).thenReturn("repo");
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

            when(projectService.getProjectWithConnections(1L)).thenReturn(project);
            when(project.getId()).thenReturn(1L);
            when(project.getName()).thenReturn("Test Project");
            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), any(), any()))
                    .thenReturn(Optional.of("lock-key"));

            when(vcsClientProvider.getHttpClient(vcsConnection)).thenReturn(httpClient);
            when(vcsServiceFactory.getOperationsService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(operationsService);

            // Simulate finding a PR for the commit
            when(operationsService.findPullRequestForCommit(any(), anyString(), anyString(), anyString()))
                    .thenReturn(42L);

            String diff = "diff --git a/file.java b/file.java";
            when(operationsService.getPullRequestDiff(any(), anyString(), anyString(), eq("42"))).thenReturn(diff);

            when(branchRepository.findByProjectIdAndBranchName(any(), anyString())).thenReturn(Optional.of(branch));
            when(branchRepository.findByIdWithIssues(any())).thenReturn(Optional.of(branch));
            when(branchRepository.save(any())).thenReturn(branch);

            processor.process(request, consumer);

            // Should use PR diff instead of commit diff
            verify(operationsService).getPullRequestDiff(any(), anyString(), anyString(), eq("42"));
            verify(operationsService, never()).getCommitDiff(any(), anyString(), anyString(), anyString());
        }
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
                    null, // ragOperationsService
                    null  // eventPublisher
            );

            assertThat(processorWithoutOptional).isNotNull();
        }
    }
}

package org.rostilos.codecrow.pipelineagent;

import okhttp3.OkHttpClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.analysisengine.dto.request.processor.AnalysisProcessRequest;
import org.rostilos.codecrow.analysisengine.processor.analysis.BranchAnalysisProcessor;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.BranchArchiveService;
import org.rostilos.codecrow.analysisengine.service.branch.BranchDiffFetcher;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.commitgraph.dag.CommitRangeContext;
import org.rostilos.codecrow.commitgraph.service.AnalyzedCommitService;
import org.rostilos.codecrow.commitgraph.service.BranchCommitService;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueScope;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectAiConnectionBinding;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.EVcsSetupStatus;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.util.tracking.LineHashSequence;
import org.rostilos.codecrow.core.util.tracking.TrackingConfidence;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.anySet;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.atLeastOnce;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class BranchResolverFlowIT extends BasePipelineAgentIT {

    private static final String APP_PATH = "src/App.java";
    private static final String OPEN_PATH = "src/Open.java";
    private static final String DELETED_PATH = "src/Deleted.java";

    @MockBean private AiAnalysisClient aiAnalysisClient;
    @MockBean private VcsServiceFactory vcsServiceFactory;
    @MockBean private AnalysisLockService analysisLockService;
    @MockBean private BranchDiffFetcher branchDiffFetcher;
    @MockBean private BranchCommitService branchCommitService;
    @MockBean private BranchArchiveService branchArchiveService;
    @MockBean private VcsClientProvider vcsClientProvider;
    @MockBean private RagOperationsService ragOperationsService;
    @MockBean private AnalyzedCommitService analyzedCommitService;

    private VcsAiClientService vcsAiClientService;
    private VcsOperationsService vcsOperationsService;

    @Autowired private BranchRepository branchRepository;
    @Autowired private BranchIssueRepository branchIssueRepository;
    @Autowired private BranchAnalysisProcessor branchAnalysisProcessor;
    @Autowired private TransactionTemplate transactionTemplate;

    @BeforeEach
    void configureMocks() throws Exception {
        vcsAiClientService = mock(VcsAiClientService.class);
        vcsOperationsService = mock(VcsOperationsService.class);

        when(analysisLockService.acquireLockWithWait(any(Project.class), anyString(), any(), anyString(), any(), any()))
                .thenReturn(Optional.of("branch-resolver-it-lock"));
        when(analysisLockService.isLocked(any(), anyString(), any())).thenReturn(false);

        when(vcsServiceFactory.getAiClientService(EVcsProvider.GITHUB)).thenReturn(vcsAiClientService);
        when(vcsServiceFactory.getOperationsService(EVcsProvider.GITHUB)).thenReturn(vcsOperationsService);
        when(vcsClientProvider.getHttpClient(any(VcsConnection.class))).thenReturn(new OkHttpClient());
        when(ragOperationsService.isRagEnabled(any(Project.class))).thenReturn(false);

        when(vcsOperationsService.checkFileExistsInBranch(
                any(OkHttpClient.class), anyString(), anyString(), anyString(), anyString()))
                .thenAnswer(inv -> !DELETED_PATH.equals(inv.getArgument(4)));

        when(branchCommitService.resolveCommitRange(any(Project.class), any(VcsConnection.class), anyString(), anyString()))
                .thenAnswer(inv -> {
                    String headCommit = inv.getArgument(3, String.class);
                    return new CommitRangeContext(List.of(headCommit), "base-commit", false);
                });

        when(branchDiffFetcher.fetchDiff(any(), any(), any(), any(), any(), any(), any(), anyList()))
                .thenAnswer(inv -> diffForCommit(inv.getArgument(0, org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest.class)
                        .getCommitHash()));

        when(branchArchiveService.downloadAndExtractFiles(any(), anyString(), anyString(), anyString(), anySet()))
                .thenAnswer(inv -> archiveForCommit(inv.getArgument(3)));

        when(vcsAiClientService.buildAiAnalysisRequestsForBranchReconciliation(
                any(Project.class), any(AnalysisProcessRequest.class), anyList(), anyMap(), any()))
                .thenAnswer(inv -> {
                    Project project = inv.getArgument(0);
                    AnalysisProcessRequest request = inv.getArgument(1);
                    @SuppressWarnings("unchecked")
                    List<org.rostilos.codecrow.analysisengine.dto.request.ai.AiRequestPreviousIssueDTO> previousIssues =
                            inv.getArgument(2);
                    @SuppressWarnings("unchecked")
                    Map<String, String> fileContents = inv.getArgument(3);
                    String relevantDiff = inv.getArgument(4);

                    return List.of(AiAnalysisRequestImpl.builder()
                            .withProjectId(project.getId())
                            .withProjectMetadata(project.getWorkspace().getName(), project.getNamespace())
                            .withProjectVcsConnectionBindingInfo("codecrow-fixtures", "branch-resolver")
                            .withProjectAiConnectionTokenDecrypted("key")
                            .withMaxAllowedTokens(1000)
                            .withAnalysisType(request.getAnalysisType())
                            .withTargetBranchName(request.getTargetBranchName())
                            .withCurrentCommitHash(request.getCommitHash())
                            .withPreviousIssues(previousIssues)
                            .withReconciliationFileContents(fileContents)
                            .withRawDiff(relevantDiff)
                            .build());
                });

        when(aiAnalysisClient.performAnalysis(any(AiAnalysisRequest.class), any()))
                .thenAnswer(inv -> aiResponseFor(inv.getArgument(0, AiAnalysisRequest.class)));
    }

    @Test
    void branchAnalysis_shouldReconcileLineShiftWhenCodeStillExists() {
        Long projectId = createProjectWithConnections();
        Long issueId = seedBranchIssue(projectId, APP_PATH, 5, "dangerousCall(userInput);", "Dangerous call remains");

        postBranch(projectId, "line-shift-commit");

        BranchIssue issue = awaitIssue(projectId, issueId, "line-shift-commit", current ->
                assertThat(current.getCurrentLineNumber()).isEqualTo(6));
        assertThat(issue.isResolved()).isFalse();
        assertThat(issue.getCurrentLineNumber()).isEqualTo(6);
        assertThat(issue.getCurrentLineHash()).isEqualTo(LineHashSequence.hashLine("        dangerousCall(userInput);"));
        assertThat(issue.getLastVerifiedCommit()).isEqualTo("line-shift-commit");
        assertThat(issue.getTrackingConfidence()).isEqualTo(TrackingConfidence.EXACT);
    }

    @Test
    void branchAnalysis_shouldResolveIssueWhenFileIsDeleted() throws Exception {
        Long projectId = createProjectWithConnections();
        Long issueId = seedBranchIssue(projectId, DELETED_PATH, 3, "legacyCall();", "Deleted file issue");

        postBranch(projectId, "delete-file-commit");

        BranchIssue issue = awaitIssue(projectId, issueId, "delete-file-commit", current ->
                assertThat(current.isResolved()).isTrue());
        assertThat(issue.isResolved()).isTrue();
        assertThat(issue.getResolvedBy()).isEqualTo("file-deletion");
        assertThat(issue.getResolvedInCommitHash()).isEqualTo("delete-file-commit");
        assertThat(issue.getResolvedInPrNumber()).isEqualTo(42L);
        assertThat(issue.getResolvedDescription()).contains("File deleted");
        verify(aiAnalysisClient, never()).performAnalysis(any(AiAnalysisRequest.class), any());
    }

    @Test
    void branchAnalysis_shouldResolveChangedContentWhenAiConfirmsFix() {
        Long projectId = createProjectWithConnections();
        Long issueId = seedBranchIssue(projectId, APP_PATH, 5, "dangerousCall(userInput);", "Dangerous call remains");

        postBranch(projectId, "ai-resolved-commit");

        BranchIssue issue = awaitIssue(projectId, issueId, "ai-resolved-commit", current ->
                assertThat(current.isResolved()).isTrue());
        assertThat(issue.isResolved()).isTrue();
        assertThat(issue.getResolvedBy()).isEqualTo("AI-reconciliation");
        assertThat(issue.getResolvedInCommitHash()).isEqualTo("ai-resolved-commit");
        assertThat(issue.getResolvedInPrNumber()).isEqualTo(42L);
        assertThat(issue.getResolvedDescription()).isEqualTo("Dangerous call was replaced with a validated safe path.");
    }

    @Test
    void branchAnalysis_shouldKeepChangedContentOpenWhenAiDoesNotResolve() {
        Long projectId = createProjectWithConnections();
        Long issueId = seedBranchIssue(projectId, APP_PATH, 5, "dangerousCall(userInput);", "Dangerous call remains");

        postBranch(projectId, "ai-open-commit");

        BranchIssue issue = awaitIssue(projectId, issueId, "ai-open-commit", current ->
                assertThat(current.isResolved()).isFalse());
        assertThat(issue.isResolved()).isFalse();
        assertThat(issue.getResolvedAt()).isNull();
        assertThat(issue.getResolvedDescription()).isNull();
    }

    @Test
    void fullReconcile_shouldProcessAllUnresolvedBranchIssuesAcrossFiles() throws Exception {
        Long projectId = createProjectWithConnections();
        Long aiResolvedIssueId = seedBranchIssue(
                projectId, APP_PATH, 5, "dangerousCall(userInput);", "Dangerous call remains");
        Long deletedIssueId = seedAdditionalBranchIssue(
                projectId, DELETED_PATH, 3, "legacyCall();", "Deleted file issue");
        Long stillOpenIssueId = seedAdditionalBranchIssue(
                projectId, OPEN_PATH, 5, "dangerousCall(normalize(userInput));", "Still open issue");
        updateBranchCommit(projectId, "full-reconcile-commit");

        Map<String, Object> result = branchAnalysisProcessor.fullReconcile(projectId, "main", event -> { });

        assertThat(result).containsEntry("status", "completed");
        assertThat(result).containsEntry("filesChecked", 3);
        assertThat(result).containsEntry("totalIssues", 1L);
        assertThat(result).containsEntry("resolvedIssues", 2L);

        BranchIssue aiResolved = branchIssueRepository.findById(aiResolvedIssueId).orElseThrow();
        assertThat(aiResolved.isResolved()).isTrue();
        assertThat(aiResolved.getResolvedBy()).isEqualTo("AI-reconciliation");
        assertThat(aiResolved.getResolvedInCommitHash()).isEqualTo("full-reconcile-commit");
        assertThat(aiResolved.getResolvedDescription()).isEqualTo("Dangerous call was replaced with a validated safe path.");

        BranchIssue deleted = branchIssueRepository.findById(deletedIssueId).orElseThrow();
        assertThat(deleted.isResolved()).isTrue();
        assertThat(deleted.getResolvedBy()).isEqualTo("file-deletion");
        assertThat(deleted.getResolvedInCommitHash()).isEqualTo("full-reconcile-commit");

        BranchIssue stillOpen = branchIssueRepository.findById(stillOpenIssueId).orElseThrow();
        assertThat(stillOpen.isResolved()).isFalse();
        assertThat(stillOpen.getLastVerifiedCommit()).isEqualTo("full-reconcile-commit");
        assertThat(stillOpen.getTrackingConfidence()).isEqualTo(TrackingConfidence.EXACT);

        var requestCaptor = org.mockito.ArgumentCaptor.forClass(AiAnalysisRequest.class);
        verify(aiAnalysisClient, atLeastOnce()).performAnalysis(requestCaptor.capture(), any());
        assertThat(requestCaptor.getAllValues())
                .anySatisfy(request -> assertThat(request.getPreviousCodeAnalysisIssues())
                        .extracting("id")
                        .contains(String.valueOf(aiResolvedIssueId)));
    }

    private void postBranch(Long projectId, String commitHash) {
        projectAuthRequest(projectId)
                .body("""
                    {
                      "projectId": %d,
                      "targetBranchName": "main",
                      "commitHash": "%s",
                      "analysisType": "BRANCH_ANALYSIS",
                      "sourcePrNumber": 42
                    }
                    """.formatted(projectId, commitHash))
                .when()
                .post("/api/processing/webhook/branch")
                .then()
                .statusCode(200);
    }

    private BranchIssue awaitIssue(Long projectId, Long issueId, String expectedCommit, Consumer<BranchIssue> assertions) {
        await().atMost(Duration.ofSeconds(10)).untilAsserted(() -> {
            Branch branch = branchRepository.findByProjectIdAndBranchName(projectId, "main").orElseThrow();
            assertThat(branch.getLastSuccessfulCommitHash()).isEqualTo(expectedCommit);

            BranchIssue issue = branchIssueRepository.findById(issueId).orElseThrow();
            assertions.accept(issue);
        });

        return branchIssueRepository.findById(issueId).orElseThrow();
    }

    private Map<String, Object> aiResponseFor(AiAnalysisRequest request) {
        if ("ai-resolved-commit".equals(request.getCurrentCommitHash())
                || "full-reconcile-commit".equals(request.getCurrentCommitHash())) {
            String issueId = request.getPreviousCodeAnalysisIssues().get(0).id();
            return Map.of(
                    "comment", "resolved",
                    "issues", List.of(Map.of(
                            "issueId", issueId,
                            "isResolved", true,
                            "resolutionReason", "Dangerous call was replaced with a validated safe path.")));
        }
        return Map.of("comment", "still open", "issues", List.of());
    }

    private String diffForCommit(String commitHash) {
        return switch (commitHash) {
            case "line-shift-commit" -> """
                    diff --git a/src/App.java b/src/App.java
                    index 1111111..2222222 100644
                    --- a/src/App.java
                    +++ b/src/App.java
                    @@ -1,7 +1,8 @@
                     package demo;

                     class App {
                         void run() {
                    +        audit();
                             dangerousCall(userInput);
                         }
                     }
                    """;
            case "delete-file-commit" -> """
                    diff --git a/src/Deleted.java b/src/Deleted.java
                    deleted file mode 100644
                    index 1111111..0000000
                    --- a/src/Deleted.java
                    +++ /dev/null
                    @@ -1,5 +0,0 @@
                    -package demo;
                    -
                    -class Deleted {
                    -    void run() { legacyCall(); }
                    -}
                    """;
            case "ai-resolved-commit" -> changedContentDiff("safeCall(validate(userInput));");
            case "ai-open-commit" -> changedContentDiff("dangerousCall(normalize(userInput));");
            case "full-reconcile-commit" -> "";
            default -> throw new IllegalArgumentException("Unknown fixture commit " + commitHash);
        };
    }

    private String changedContentDiff(String replacement) {
        return """
                diff --git a/src/App.java b/src/App.java
                index 1111111..3333333 100644
                --- a/src/App.java
                +++ b/src/App.java
                @@ -1,7 +1,7 @@
                 package demo;

                 class App {
                     void run() {
                -        dangerousCall(userInput);
                +        %s
                     }
                 }
                """.formatted(replacement);
    }

    private Map<String, String> archiveForCommit(String commitHash) {
        Map<String, String> contents = new LinkedHashMap<>();
        switch (commitHash) {
            case "line-shift-commit" -> contents.put(APP_PATH, """
                    package demo;

                    class App {
                        void run() {
                            audit();
                            dangerousCall(userInput);
                        }
                    }
                    """);
            case "ai-resolved-commit" -> contents.put(APP_PATH, """
                    package demo;

                    class App {
                        void run() {
                            safeCall(validate(userInput));
                        }
                    }
                    """);
            case "ai-open-commit" -> contents.put(APP_PATH, """
                    package demo;

                    class App {
                        void run() {
                            dangerousCall(normalize(userInput));
                        }
                    }
                    """);
            case "full-reconcile-commit" -> {
                contents.put(APP_PATH, """
                        package demo;

                        class App {
                            void run() {
                                safeCall(validate(userInput));
                            }
                        }
                        """);
                contents.put(OPEN_PATH, """
                        package demo;

                        class Open {
                            void run() {
                                dangerousCall(normalize(userInput));
                            }
                        }
                        """);
            }
            case "delete-file-commit" -> { }
            default -> throw new IllegalArgumentException("Unknown fixture commit " + commitHash);
        }
        return contents;
    }

    private Long seedBranchIssue(Long projectId, String filePath, int line, String snippet, String title) {
        return transactionTemplate.execute(status -> {
            Project project = entityManager.find(Project.class, projectId);

            Branch branch = new Branch();
            branch.setProject(project);
            branch.setBranchName("main");
            branch.setCommitHash("base-commit");
            branch.setLastSuccessfulCommitHash("base-commit");
            branch.setLastKnownHeadCommit("base-commit");
            entityManager.persist(branch);

            BranchIssue issue = new BranchIssue();
            issue.setBranch(branch);
            issue.setSeverity(IssueSeverity.HIGH);
            issue.setFilePath(filePath);
            issue.setLineNumber(line);
            issue.setCurrentLineNumber(line);
            issue.setIssueScope(IssueScope.LINE);
            issue.setIssueCategory(IssueCategory.SECURITY);
            issue.setTitle(title);
            issue.setReason("Fixture branch resolver issue");
            issue.setCodeSnippet(snippet);
            issue.setLineHash(LineHashSequence.hashLine(snippet));
            issue.setCurrentLineHash(LineHashSequence.hashLine(snippet));
            issue.setContentFingerprint(filePath + ":" + title);
            entityManager.persist(issue);
            entityManager.flush();

            return issue.getId();
        });
    }

    private Long seedAdditionalBranchIssue(Long projectId, String filePath, int line, String snippet, String title) {
        return transactionTemplate.execute(status -> {
            Branch branch = branchRepository.findByProjectIdAndBranchName(projectId, "main").orElseThrow();

            BranchIssue issue = new BranchIssue();
            issue.setBranch(branch);
            issue.setSeverity(IssueSeverity.HIGH);
            issue.setFilePath(filePath);
            issue.setLineNumber(line);
            issue.setCurrentLineNumber(line);
            issue.setIssueScope(IssueScope.LINE);
            issue.setIssueCategory(IssueCategory.SECURITY);
            issue.setTitle(title);
            issue.setReason("Fixture branch resolver issue");
            issue.setCodeSnippet(snippet);
            issue.setLineHash(LineHashSequence.hashLine(snippet));
            issue.setCurrentLineHash(LineHashSequence.hashLine(snippet));
            issue.setContentFingerprint(filePath + ":" + title);
            entityManager.persist(issue);
            entityManager.flush();

            return issue.getId();
        });
    }

    private void updateBranchCommit(Long projectId, String commitHash) {
        transactionTemplate.executeWithoutResult(status -> {
            Branch branch = branchRepository.findByProjectIdAndBranchName(projectId, "main").orElseThrow();
            branch.setCommitHash(commitHash);
            branch.setLastSuccessfulCommitHash(commitHash);
            branch.setLastKnownHeadCommit(commitHash);
            branchRepository.save(branch);
        });
    }

    private Long createProjectWithConnections() {
        return transactionTemplate.execute(status -> {
            Workspace workspace = new Workspace("branch-resolver-ws", "Branch Resolver WS", "IT workspace");
            entityManager.persist(workspace);

            Project project = new Project();
            project.setWorkspace(workspace);
            project.setNamespace("branch-resolver");
            project.setName("Branch Resolver");
            entityManager.persist(project);

            VcsConnection vcsConnection = new VcsConnection();
            vcsConnection.setWorkspace(workspace);
            vcsConnection.setConnectionName("GitHub fixture");
            vcsConnection.setProviderType(EVcsProvider.GITHUB);
            vcsConnection.setConnectionType(EVcsConnectionType.GITHUB_APP);
            vcsConnection.setSetupStatus(EVcsSetupStatus.CONNECTED);
            vcsConnection.setExternalWorkspaceId("fixture-workspace");
            vcsConnection.setExternalWorkspaceSlug("codecrow-fixtures");
            entityManager.persist(vcsConnection);

            VcsRepoBinding repoBinding = new VcsRepoBinding();
            repoBinding.setWorkspace(workspace);
            repoBinding.setProject(project);
            repoBinding.setVcsConnection(vcsConnection);
            repoBinding.setProvider(EVcsProvider.GITHUB);
            repoBinding.setExternalRepoId("branch-resolver-repo");
            repoBinding.setExternalNamespace("codecrow-fixtures");
            repoBinding.setExternalRepoSlug("branch-resolver");
            repoBinding.setDisplayName("branch-resolver");
            repoBinding.setDefaultBranch("main");
            entityManager.persist(repoBinding);
            project.setVcsRepoBinding(repoBinding);

            AIConnection aiConnection = new AIConnection();
            aiConnection.setWorkspace(workspace);
            aiConnection.setName("Fixture AI");
            aiConnection.setProviderKey(AIProviderKey.OPENAI);
            aiConnection.setAiModel("fixture-model");
            aiConnection.setApiKeyEncrypted("encrypted-fixture-key");
            entityManager.persist(aiConnection);

            ProjectAiConnectionBinding aiBinding = new ProjectAiConnectionBinding();
            aiBinding.setProject(project);
            aiBinding.setAiConnection(aiConnection);
            entityManager.persist(aiBinding);
            project.setAiConnectionBinding(aiBinding);

            entityManager.flush();
            return project.getId();
        });
    }
}

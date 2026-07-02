package org.rostilos.codecrow.pipelineagent;

import okhttp3.OkHttpClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.FileContentDto;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.analysisengine.dto.request.processor.AnalysisProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.BranchArchiveService;
import org.rostilos.codecrow.analysisengine.service.branch.BranchDiffFetcher;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsReportingService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.commitgraph.dag.CommitRangeContext;
import org.rostilos.codecrow.commitgraph.service.AnalyzedCommitService;
import org.rostilos.codecrow.commitgraph.service.BranchCommitService;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
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
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.transaction.support.TransactionTemplate;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class LineTrackingFlowIT extends BasePipelineAgentIT {

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
    private VcsReportingService vcsReportingService;
    private VcsOperationsService vcsOperationsService;

    @Autowired private CodeAnalysisRepository codeAnalysisRepository;
    @Autowired private BranchRepository branchRepository;
    @Autowired private BranchIssueRepository branchIssueRepository;
    @Autowired private TransactionTemplate transactionTemplate;

    @BeforeEach
    void configureMocks() throws Exception {
        vcsAiClientService = mock(VcsAiClientService.class);
        vcsReportingService = mock(VcsReportingService.class);
        vcsOperationsService = mock(VcsOperationsService.class);

        when(analysisLockService.acquireLockWithWait(any(Project.class), anyString(), any(), anyString(), any(), any()))
                .thenReturn(Optional.of("it-lock"));
        when(analysisLockService.isLocked(anyLong(), anyString(), any())).thenReturn(false);

        when(vcsServiceFactory.getAiClientService(EVcsProvider.GITHUB)).thenReturn(vcsAiClientService);
        when(vcsServiceFactory.getReportingService(EVcsProvider.GITHUB)).thenReturn(vcsReportingService);
        when(vcsServiceFactory.getOperationsService(EVcsProvider.GITHUB)).thenReturn(vcsOperationsService);
        when(vcsClientProvider.getHttpClient(any(VcsConnection.class))).thenReturn(new OkHttpClient());
        when(ragOperationsService.isRagEnabled(any(Project.class))).thenReturn(false);

        when(vcsAiClientService.buildAiAnalysisRequests(
                any(Project.class), any(AnalysisProcessRequest.class), any(), anyList()))
                .thenAnswer(inv -> {
                    Project project = inv.getArgument(0);
                    PrProcessRequest request = inv.getArgument(1);
                    return List.of(aiRequest(project, request));
                });

        when(aiAnalysisClient.performAnalysis(any(AiAnalysisRequest.class), any()))
                .thenAnswer(inv -> aiResponse(((AiAnalysisRequest) inv.getArgument(0)).getCurrentCommitHash()));

        when(branchCommitService.resolveCommitRange(any(Project.class), any(VcsConnection.class), anyString(), anyString()))
                .thenAnswer(inv -> CommitRangeContext.firstAnalysis(inv.getArgument(3)));
        when(branchDiffFetcher.fetchDiff(any(), any(), any(), any(), any(), any(), any(), anyList()))
                .thenReturn(resource("line-tracking/diffs/merge-pr3.diff"));
        when(branchArchiveService.downloadAndExtractFiles(any(), anyString(), anyString(), anyString(), anySet()))
                .thenAnswer(inv -> {
                    @SuppressWarnings("unchecked")
                    Set<String> needed = inv.getArgument(4);
                    Map<String, String> contents = new LinkedHashMap<>();
                    String app = resource("line-tracking/files/pr3/src/App.java");
                    for (String file : needed) {
                        if ("src/App.java".equals(file)) {
                            contents.put(file, app);
                        }
                    }
                    return contents;
                });
    }

    @Test
    void prIterationsAndMerge_shouldTrackShiftedIssuesAndExcludeFixedOlderIterations() throws Exception {
        Long projectId = createProjectWithConnections();

        postPr(projectId, "pr1-commit");
        postPr(projectId, "pr2-commit");
        postPr(projectId, "pr3-commit");

        await().atMost(Duration.ofSeconds(10)).untilAsserted(() ->
                assertThat(codeAnalysisRepository.findAllByProjectIdAndPrNumberOrderByPrVersionDesc(projectId, 42L))
                        .hasSize(3));

        List<CodeAnalysis> analyses = codeAnalysisRepository
                .findAllByProjectIdAndPrNumberOrderByPrVersionDesc(projectId, 42L);
        CodeAnalysis pr3 = analyses.get(0);
        CodeAnalysis pr2 = analyses.get(1);
        CodeAnalysis pr1 = analyses.get(2);

        CodeAnalysisIssue pr1Risky = issue(pr1, "Risky call remains");
        CodeAnalysisIssue pr2Risky = issue(pr2, "Risky call remains");
        CodeAnalysisIssue pr2Leak = issue(pr2, "Secret leak remains");
        CodeAnalysisIssue pr3Leak = issue(pr3, "Secret leak remains");

        assertThat(pr1.getIssues()).hasSize(1);
        assertThat(pr1Risky.getLineNumber()).isEqualTo(5);
        assertThat(pr2Risky.getLineNumber()).isEqualTo(6);
        assertThat(pr2Risky.getTrackedFromIssueId()).isEqualTo(pr1Risky.getId());
        assertThat(pr2Risky.isResolved()).isTrue();
        assertThat(pr2Risky.getResolvedByPr()).isEqualTo(42L);
        assertThat(pr2Risky.getResolvedCommitHash()).isEqualTo("pr3-commit");
        assertThat(pr2Leak.getLineNumber()).isEqualTo(7);
        assertThat(pr3Leak.getLineNumber()).isEqualTo(6);
        assertThat(pr3Leak.getTrackedFromIssueId()).isEqualTo(pr2Leak.getId());

        postBranchMerge(projectId);

        List<BranchIssue> branchIssues = await().atMost(Duration.ofSeconds(10)).until(() ->
                branchRepository.findByProjectIdAndBranchName(projectId, "main")
                        .map(branch -> {
                            List<BranchIssue> issues = branchIssueRepository.findByBranchId(branch.getId());
                            if (issues.size() != 1) {
                                return null;
                            }
                            BranchIssue issue = issues.get(0);
                            return "merge-pr3-commit".equals(issue.getLastVerifiedCommit()) ? issues : null;
                        })
                        .orElse(null),
                java.util.Objects::nonNull);

        assertThat(branchIssues).hasSize(1);
        BranchIssue branchIssue = branchIssues.get(0);
        assertThat(branchIssue.getTitle()).isEqualTo("Secret leak remains");
        assertThat(branchIssue.getCurrentLineNumber()).isEqualTo(6);
        assertThat(branchIssue.getCurrentLineHash()).isNotBlank();
        assertThat(branchIssue.getLastVerifiedCommit()).isEqualTo("merge-pr3-commit");
        assertThat(branchIssue.isResolved()).isFalse();
    }

    private void postPr(Long projectId, String commitHash) {
        projectAuthRequest(projectId)
                .body("""
                    {
                      "projectId": %d,
                      "pullRequestId": 42,
                      "targetBranchName": "main",
                      "sourceBranchName": "feature/line-tracking",
                      "commitHash": "%s",
                      "analysisType": "PR_REVIEW",
                      "prAuthorId": "author-1",
                      "prAuthorUsername": "alice"
                    }
                    """.formatted(projectId, commitHash))
                .when()
                .post("/api/processing/webhook/pr")
                .then()
                .statusCode(200);
    }

    private void postBranchMerge(Long projectId) {
        projectAuthRequest(projectId)
                .body("""
                    {
                      "projectId": %d,
                      "targetBranchName": "main",
                      "commitHash": "merge-pr3-commit",
                      "analysisType": "BRANCH_ANALYSIS",
                      "sourcePrNumber": 42
                    }
                    """.formatted(projectId))
                .when()
                .post("/api/processing/webhook/branch")
                .then()
                .statusCode(200);
    }

    private AiAnalysisRequest aiRequest(Project project, PrProcessRequest request) throws Exception {
        String key = switch (request.getCommitHash()) {
            case "pr1-commit" -> "pr1";
            case "pr2-commit" -> "pr2";
            case "pr3-commit" -> "pr3";
            default -> throw new IllegalArgumentException("Unknown fixture commit " + request.getCommitHash());
        };
        String content = resource("line-tracking/files/" + key + "/src/App.java");
        String diff = resource("line-tracking/diffs/" + key + ".diff");
        PrEnrichmentDataDto enrichment = new PrEnrichmentDataDto(
                List.of(FileContentDto.of("src/App.java", content)),
                List.of(),
                List.of(),
                PrEnrichmentDataDto.EnrichmentStats.empty());

        return AiAnalysisRequestImpl.builder()
                .withProjectId(project.getId())
                .withProjectMetadata(project.getWorkspace().getName(), project.getNamespace())
                .withProjectVcsConnectionBindingInfo("codecrow-fixtures", "line-tracking")
                .withPullRequestId(request.getPullRequestId())
                .withAnalysisType(AnalysisType.PR_REVIEW)
                .withTargetBranchName(request.getTargetBranchName())
                .withSourceBranchName(request.getSourceBranchName())
                .withVcsProvider(EVcsProvider.GITHUB.getId())
                .withChangedFiles(List.of("src/App.java"))
                .withDeletedFiles(List.of())
                .withDiffSnippets(List.of(diff))
                .withRawDiff(diff)
                .withCurrentCommitHash(request.getCommitHash())
                .withEnrichmentData(enrichment)
                .build();
    }

    private Map<String, Object> aiResponse(String commitHash) throws Exception {
        String key = switch (commitHash) {
            case "pr1-commit" -> "pr1";
            case "pr2-commit" -> "pr2";
            case "pr3-commit" -> "pr3";
            default -> throw new IllegalArgumentException("Unknown fixture commit " + commitHash);
        };
        return objectMapper.readValue(resource("line-tracking/ai/" + key + ".json"), Map.class);
    }

    private static CodeAnalysisIssue issue(CodeAnalysis analysis, String title) {
        return analysis.getIssues().stream()
                .filter(issue -> title.equals(issue.getTitle()))
                .findFirst()
                .orElseThrow();
    }

    private String resource(String path) throws Exception {
        var url = Thread.currentThread().getContextClassLoader().getResource(path);
        assertThat(url).as(path).isNotNull();
        return Files.readString(Path.of(url.toURI()));
    }

    Long createProjectWithConnections() {
        return transactionTemplate.execute(status -> {
            Workspace workspace = new Workspace("line-tracking-ws", "Line Tracking WS", "IT workspace");
            entityManager.persist(workspace);

            Project project = new Project();
            project.setWorkspace(workspace);
            project.setNamespace("line-tracking");
            project.setName("Line Tracking");
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
            repoBinding.setExternalRepoId("line-tracking-repo");
            repoBinding.setExternalNamespace("codecrow-fixtures");
            repoBinding.setExternalRepoSlug("line-tracking");
            repoBinding.setDisplayName("line-tracking");
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

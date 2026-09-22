package org.rostilos.codecrow.pipelineagent.generic.service;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.service.pr.PullRequestDiffPreparationService;
import org.rostilos.codecrow.analysisengine.util.AnalysisLimitEnforcer;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectAiConnectionBinding;
import org.rostilos.codecrow.core.model.project.config.AnalysisScopeConfig;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.model.VcsCommit;
import org.rostilos.codecrow.vcsclient.model.VcsPullRequest;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class AbstractVcsAiClientServiceCommitIdentityTest {
    private static final String SHA1 =
            "eb59a730e56532cc96d0e9fbb6b7616d6ca9897e";
    private static final String SHA256 =
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

    @Test
    void acceptsOnlyFullProviderObjectIds() {
        assertThat(AbstractVcsAiClientService.isFullGitObjectId(SHA1)).isTrue();
        assertThat(AbstractVcsAiClientService.isFullGitObjectId(SHA256)).isTrue();
        assertThat(AbstractVcsAiClientService.isFullGitObjectId("eb59a730e565")).isFalse();
        assertThat(AbstractVcsAiClientService.isFullGitObjectId("not-a-commit")).isFalse();
        assertThat(AbstractVcsAiClientService.isFullGitObjectId(null)).isFalse();
    }

    @Test
    void bindsCanonicalBranchRevisionAsCurrentAndTargetHead() throws Exception {
        Project project = mock(Project.class);
        VcsRepoInfo repository = mock(VcsRepoInfo.class);
        VcsConnection connection = mock(VcsConnection.class);
        ProjectAiConnectionBinding aiBinding = mock(ProjectAiConnectionBinding.class);
        AIConnection aiConnection = mock(AIConnection.class);
        Workspace workspace = mock(Workspace.class);
        TokenEncryptionService encryptionService = mock(TokenEncryptionService.class);
        VcsClientProvider clientProvider = mock(VcsClientProvider.class);
        VcsClient client = mock(VcsClient.class);

        when(project.getId()).thenReturn(7L);
        when(project.getNamespace()).thenReturn("repository");
        when(project.getWorkspace()).thenReturn(workspace);
        when(workspace.getName()).thenReturn("tenant-workspace");
        when(project.getEffectiveConfig()).thenReturn(new ProjectConfig(false, "main",
                null, null, true, true, null, null));
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repository);
        when(repository.getVcsConnection()).thenReturn(connection);
        when(repository.getRepoWorkspace()).thenReturn("provider-workspace");
        when(repository.getRepoSlug()).thenReturn("repository");
        when(project.getAiBinding()).thenReturn(aiBinding);
        when(aiBinding.getAiConnection()).thenReturn(aiConnection);
        when(aiConnection.getProviderKey()).thenReturn(AIProviderKey.OPENAI);
        when(aiConnection.getAiModel()).thenReturn("review-model");
        when(aiConnection.getApiKeyEncrypted()).thenReturn("encrypted-ai-key");
        when(encryptionService.decrypt("encrypted-ai-key")).thenReturn("ai-key");
        when(encryptionService.decrypt("encrypted-vcs-token")).thenReturn("vcs-token");
        when(connection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
        when(connection.getConnectionType()).thenReturn(EVcsConnectionType.ACCESS_TOKEN);
        when(connection.getAccessToken()).thenReturn("encrypted-vcs-token");
        when(clientProvider.getClient(connection)).thenReturn(client);
        when(client.getCommitHistory(
                "provider-workspace", "repository", "eb59a730e565", 1))
                .thenReturn(List.of(new VcsCommit(SHA1, null, null, null, null, List.of())));

        BranchProcessRequest request = new BranchProcessRequest();
        request.projectId = 7L;
        request.targetBranchName = "main";
        request.commitHash = "eb59a730e565";
        request.analysisType = AnalysisType.BRANCH_ANALYSIS;
        TestAiClientService service = new TestAiClientService(
                encryptionService, clientProvider);

        AiAnalysisRequest directPush = service.buildDirectPushAnalysisRequests(
                project,
                request,
                "diff --git a/src/App.java b/src/App.java\n+change\n",
                Map.of(),
                List.of("src/App.java"))
                .get(0);

        assertThat(request.getCommitHash()).isEqualTo(SHA1);
        assertThat(directPush.getCurrentCommitHash()).isEqualTo(SHA1);
        assertThat(directPush.getTargetHeadCommitHash()).isEqualTo(SHA1);

        BranchProcessRequest reconciliationRequest = new BranchProcessRequest();
        reconciliationRequest.projectId = 7L;
        reconciliationRequest.targetBranchName = "main";
        reconciliationRequest.commitHash = SHA256;
        reconciliationRequest.analysisType = AnalysisType.BRANCH_ANALYSIS;

        AiAnalysisRequest reconciliation = service.buildAiAnalysisRequestsForBranchReconciliation(
                project,
                reconciliationRequest,
                List.of(),
                Map.of())
                .get(0);

        assertThat(reconciliation.getCurrentCommitHash()).isEqualTo(SHA256);
        assertThat(reconciliation.getTargetHeadCommitHash()).isEqualTo(SHA256);
    }

    @Test
    void keepsCompleteUnfilteredProposedTreeAlongsideIncrementalReviewScope() throws Exception {
        Project project = mock(Project.class);
        VcsRepoInfo repository = mock(VcsRepoInfo.class);
        VcsConnection connection = mock(VcsConnection.class);
        ProjectAiConnectionBinding aiBinding = mock(ProjectAiConnectionBinding.class);
        AIConnection aiConnection = mock(AIConnection.class);
        Workspace workspace = mock(Workspace.class);
        TokenEncryptionService encryptionService = mock(TokenEncryptionService.class);
        VcsClientProvider clientProvider = mock(VcsClientProvider.class);
        VcsClient client = mock(VcsClient.class);
        CodeAnalysis previousAnalysis = mock(CodeAnalysis.class);

        ProjectConfig config = new ProjectConfig();
        config.setAnalysisScope(new AnalysisScopeConfig(List.of("src/**"), List.of()));
        when(project.getId()).thenReturn(7L);
        when(project.getNamespace()).thenReturn("repository");
        when(project.getWorkspace()).thenReturn(workspace);
        when(workspace.getName()).thenReturn("tenant-workspace");
        when(project.getEffectiveConfig()).thenReturn(config);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repository);
        when(repository.getVcsConnection()).thenReturn(connection);
        when(repository.getRepoWorkspace()).thenReturn("provider-workspace");
        when(repository.getRepoSlug()).thenReturn("repository");
        when(project.getAiBinding()).thenReturn(aiBinding);
        when(aiBinding.getAiConnection()).thenReturn(aiConnection);
        when(aiConnection.getProviderKey()).thenReturn(AIProviderKey.OPENAI);
        when(aiConnection.getAiModel()).thenReturn("review-model");
        when(aiConnection.getApiKeyEncrypted()).thenReturn("encrypted-ai-key");
        when(encryptionService.decrypt("encrypted-ai-key")).thenReturn("ai-key");
        when(encryptionService.decrypt("encrypted-vcs-token")).thenReturn("vcs-token");
        when(connection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
        when(connection.getConnectionType()).thenReturn(EVcsConnectionType.ACCESS_TOKEN);
        when(connection.getAccessToken()).thenReturn("encrypted-vcs-token");
        when(clientProvider.getClient(connection)).thenReturn(client);
        when(previousAnalysis.getCommitHash()).thenReturn("previous-reviewed-head");

        String targetHead = "1111111111111111111111111111111111111111";
        String sourceHead = "2222222222222222222222222222222222222222";
        String fullDiff = section("src/Delta.java", "x".repeat(1800))
                + section("src/Earlier.java", "z".repeat(1800))
                + section("docs/Excluded.md", "documentation")
                + renameSection("src/OldName.java", "src/NewName.java")
                + deletedSection("assets/Removed.bin");
        String deltaDiff = section("src/Delta.java", "y".repeat(600));
        when(client.getPullRequest("provider-workspace", "repository", 42L))
                .thenReturn(new VcsPullRequest(
                        42L, "title", "description", "feature", "main",
                        targetHead, targetHead, sourceHead, "OPEN", false, "url"));
        when(client.getCommitRangeDiff(
                "provider-workspace", "repository", targetHead, sourceHead))
                .thenReturn(fullDiff);
        when(client.getCommitRangeDiff(
                "provider-workspace", "repository", "previous-reviewed-head", sourceHead))
                .thenReturn(deltaDiff);

        PrProcessRequest request = new PrProcessRequest();
        request.projectId = 7L;
        request.pullRequestId = 42L;
        request.commitHash = sourceHead;
        request.sourceBranchName = "feature";
        request.targetBranchName = "main";
        request.analysisType = AnalysisType.PR_REVIEW;
        TestAiClientService service = new TestAiClientService(
                encryptionService,
                clientProvider,
                new PullRequestDiffPreparationService(new AnalysisLimitEnforcer()));

        AiAnalysisRequest review = service.buildAiAnalysisRequests(
                project,
                request,
                Optional.of(previousAnalysis))
                .get(0);

        assertThat(review.getAnalysisMode()).isEqualTo(AnalysisMode.INCREMENTAL);
        assertThat(review.getChangedFiles()).containsExactly("src/Delta.java");
        assertThat(review.getDeletedFiles()).isEmpty();
        assertThat(review.getProposedTreeChangedFiles()).containsExactly(
                "src/Delta.java", "src/Earlier.java", "docs/Excluded.md", "src/NewName.java");
        assertThat(review.getProposedTreeDeletedFiles()).containsExactly(
                "src/OldName.java", "assets/Removed.bin");
    }

    private static String section(String path, String addedContent) {
        return "diff --git a/" + path + " b/" + path + "\n"
                + "--- a/" + path + "\n+++ b/" + path + "\n@@ -1 +1 @@\n-old\n+"
                + addedContent + "\n";
    }

    private static String deletedSection(String path) {
        return "diff --git a/" + path + " b/" + path + "\n"
                + "deleted file mode 100644\n"
                + "--- a/" + path + "\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n";
    }

    private static String renameSection(String oldPath, String newPath) {
        return "diff --git a/" + oldPath + " b/" + newPath + "\n"
                + "similarity index 95%\n"
                + "rename from " + oldPath + "\n"
                + "rename to " + newPath + "\n"
                + "--- a/" + oldPath + "\n+++ b/" + newPath + "\n";
    }

    private static final class TestAiClientService extends AbstractVcsAiClientService {
        private TestAiClientService(
                TokenEncryptionService encryptionService,
                VcsClientProvider clientProvider) {
            this(encryptionService, clientProvider, null);
        }

        private TestAiClientService(
                TokenEncryptionService encryptionService,
                VcsClientProvider clientProvider,
                PullRequestDiffPreparationService diffPreparationService) {
            super(encryptionService, clientProvider, null, null, diffPreparationService);
        }

        @Override
        public EVcsProvider getProvider() {
            return EVcsProvider.GITHUB;
        }
    }

}

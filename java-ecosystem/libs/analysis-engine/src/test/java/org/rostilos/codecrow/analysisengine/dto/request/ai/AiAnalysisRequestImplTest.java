package org.rostilos.codecrow.analysisengine.dto.request.ai;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;

import java.util.Arrays;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("AiAnalysisRequestImpl")
class AiAnalysisRequestImplTest {

    @Nested
    @DisplayName("Builder")
    class BuilderTests {

        @Test
        @DisplayName("should build with all fields")
        void shouldBuildWithAllFields() {
            List<String> changedFiles = Arrays.asList("file1.java", "file2.java");
            List<String> diffSnippets = Arrays.asList("snippet1", "snippet2");

            AiAnalysisRequestImpl request = AiAnalysisRequestImpl.builder()
                    .withProjectId(1L)
                    .withPullRequestId(100L)
                    .withProjectVcsConnectionBindingInfo("workspace", "repo-slug")
                    .withProjectAiConnectionTokenDecrypted("api-key-123")
                    .withProjectVcsConnectionCredentials("client-id", "client-secret")
                    .withAccessToken("access-token")
                    .withMaxAllowedTokens(4000)
                    .withUseLocalMcp(true)
                    .withAnalysisType(AnalysisType.PR_REVIEW)
                    .withPrTitle("PR Title")
                    .withPrDescription("PR Description")
                    .withChangedFiles(changedFiles)
                    .withDiffSnippets(diffSnippets)
                    .withProjectMetadata("proj-workspace", "proj-namespace")
                    .withTargetBranchName("main")
                    .withVcsProvider("BITBUCKET_CLOUD")
                    .withRawDiff("raw diff content")
                    .withAnalysisMode(AnalysisMode.INCREMENTAL)
                    .withDeltaDiff("delta diff")
                    .withPreviousCommitHash("abc123")
                    .withCurrentCommitHash("def456")
                    .build();

            assertThat(request.getProjectId()).isEqualTo(1L);
            assertThat(request.getPullRequestId()).isEqualTo(100L);
            assertThat(request.getProjectVcsWorkspace()).isEqualTo("workspace");
            assertThat(request.getProjectVcsRepoSlug()).isEqualTo("repo-slug");
            assertThat(request.getAiApiKey()).isEqualTo("api-key-123");
            assertThat(request.getOAuthClient()).isEqualTo("client-id");
            assertThat(request.getOAuthSecret()).isEqualTo("client-secret");
            assertThat(request.getAccessToken()).isEqualTo("access-token");
            assertThat(request.getMaxAllowedTokens()).isEqualTo(4000);
            assertThat(request.getUseLocalMcp()).isTrue();
            assertThat(request.getAnalysisType()).isEqualTo(AnalysisType.PR_REVIEW);
            assertThat(request.getPrTitle()).isEqualTo("PR Title");
            assertThat(request.getPrDescription()).isEqualTo("PR Description");
            assertThat(request.getChangedFiles()).containsExactly("file1.java", "file2.java");
            assertThat(request.getDiffSnippets()).containsExactly("snippet1", "snippet2");
            assertThat(request.getProjectWorkspace()).isEqualTo("proj-workspace");
            assertThat(request.getProjectNamespace()).isEqualTo("proj-namespace");
            assertThat(request.getTargetBranchName()).isEqualTo("main");
            assertThat(request.getVcsProvider()).isEqualTo("BITBUCKET_CLOUD");
            assertThat(request.getRawDiff()).isEqualTo("raw diff content");
            assertThat(request.getAnalysisMode()).isEqualTo(AnalysisMode.INCREMENTAL);
            assertThat(request.getDeltaDiff()).isEqualTo("delta diff");
            assertThat(request.getPreviousCommitHash()).isEqualTo("abc123");
            assertThat(request.getCurrentCommitHash()).isEqualTo("def456");
        }

        @Test
        @DisplayName("should default analysisMode to FULL when not set")
        void shouldDefaultAnalysisModeToFullWhenNotSet() {
            AiAnalysisRequestImpl request = AiAnalysisRequestImpl.builder()
                    .withProjectId(1L)
                    .build();

            assertThat(request.getAnalysisMode()).isEqualTo(AnalysisMode.FULL);
        }

        @Test
        @DisplayName("should handle null values")
        void shouldHandleNullValues() {
            AiAnalysisRequestImpl request = AiAnalysisRequestImpl.builder().build();

            assertThat(request.getProjectId()).isNull();
            assertThat(request.getPullRequestId()).isNull();
            assertThat(request.getAiApiKey()).isNull();
            assertThat(request.getChangedFiles()).isNull();
        }
    }

    @Test
    @DisplayName("should implement AiAnalysisRequest interface")
    void shouldImplementAiAnalysisRequestInterface() {
        AiAnalysisRequest request = AiAnalysisRequestImpl.builder()
                .withProjectId(1L)
                .build();

        assertThat(request).isInstanceOf(AiAnalysisRequest.class);
    }

    @Test
    @DisplayName("getPreviousCodeAnalysisIssues should return null when not set")
    void getPreviousCodeAnalysisIssuesShouldReturnNullWhenNotSet() {
        AiAnalysisRequestImpl request = AiAnalysisRequestImpl.builder().build();

        assertThat(request.getPreviousCodeAnalysisIssues()).isNull();
    }
}

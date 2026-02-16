package org.rostilos.codecrow.analysisengine.dto.request.ai;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.ProjectVcsConnectionBinding;

import java.util.Arrays;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

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

    @Nested
    @DisplayName("Builder with entity objects")
    class BuilderWithEntityObjectsTests {

        @Test
        @DisplayName("should build with AIConnection")
        void shouldBuildWithAiConnection() {
            AIConnection aiConnection = mock(AIConnection.class);
            when(aiConnection.getProviderKey()).thenReturn(AIProviderKey.ANTHROPIC);
            when(aiConnection.getAiModel()).thenReturn("claude-3-opus");

            AiAnalysisRequestImpl request = AiAnalysisRequestImpl.builder()
                    .withProjectAiConnection(aiConnection)
                    .build();

            assertThat(request.getAiProvider()).isEqualTo(AIProviderKey.ANTHROPIC);
            assertThat(request.getAiModel()).isEqualTo("claude-3-opus");
        }

        @Test
        @DisplayName("should build with ProjectVcsConnectionBinding")
        void shouldBuildWithProjectVcsConnectionBinding() {
            ProjectVcsConnectionBinding binding = mock(ProjectVcsConnectionBinding.class);
            when(binding.getWorkspace()).thenReturn("test-workspace");
            when(binding.getRepoSlug()).thenReturn("test-repo");

            AiAnalysisRequestImpl request = AiAnalysisRequestImpl.builder()
                    .withProjectVcsConnectionBinding(binding)
                    .build();

            assertThat(request.getProjectVcsWorkspace()).isEqualTo("test-workspace");
            assertThat(request.getProjectVcsRepoSlug()).isEqualTo("test-repo");
        }

        @Test
        @DisplayName("should build with previous analysis data")
        void shouldBuildWithPreviousAnalysisData() {
            CodeAnalysis previousAnalysis = mock(CodeAnalysis.class);
            CodeAnalysisIssue issue1 = new CodeAnalysisIssue();
            issue1.setFilePath("Test.java");
            issue1.setLineNumber(10);
            issue1.setReason("Test issue");
            issue1.setSeverity(IssueSeverity.HIGH);
            
            when(previousAnalysis.getIssues()).thenReturn(List.of(issue1));

            AiAnalysisRequestImpl request = AiAnalysisRequestImpl.builder()
                    .withPreviousAnalysisData(Optional.of(previousAnalysis))
                    .build();

            assertThat(request.getPreviousCodeAnalysisIssues()).hasSize(1);
        }

        @Test
        @DisplayName("should handle empty previous analysis")
        void shouldHandleEmptyPreviousAnalysis() {
            AiAnalysisRequestImpl request = AiAnalysisRequestImpl.builder()
                    .withPreviousAnalysisData(Optional.empty())
                    .build();

            assertThat(request.getPreviousCodeAnalysisIssues()).isNull();
        }
    }

    @Nested
    @DisplayName("withAllPrAnalysesData - deduplication")
    class WithAllPrAnalysesDataTests {

        private CodeAnalysisIssue createIssue(Long id, String file, int line, IssueSeverity severity,
                                               String reason, boolean resolved, int prVersion) {
            CodeAnalysisIssue issue = new CodeAnalysisIssue();
            issue.setFilePath(file);
            issue.setLineNumber(line);
            issue.setSeverity(severity);
            issue.setReason(reason);
            issue.setResolved(resolved);
            if (resolved) {
                issue.setResolvedDescription("Fixed");
                issue.setResolvedCommitHash("fix-commit");
            }
            CodeAnalysis analysis = new CodeAnalysis();
            analysis.setPrVersion(prVersion);
            issue.setAnalysis(analysis);
            return issue;
        }

        @Test
        @DisplayName("should handle null analyses list")
        void shouldHandleNullAnalysesList() {
            AiAnalysisRequestImpl request = AiAnalysisRequestImpl.builder()
                    .withAllPrAnalysesData(null)
                    .build();
            assertThat(request.getPreviousCodeAnalysisIssues()).isNull();
        }

        @Test
        @DisplayName("should handle empty analyses list")
        void shouldHandleEmptyAnalysesList() {
            AiAnalysisRequestImpl request = AiAnalysisRequestImpl.builder()
                    .withAllPrAnalysesData(List.of())
                    .build();
            assertThat(request.getPreviousCodeAnalysisIssues()).isNull();
        }

        @Test
        @DisplayName("should deduplicate issues across versions - keep newer")
        void shouldDeduplicateKeepNewer() {
            CodeAnalysisIssue v1Issue = createIssue(1L, "File.java", 10, IssueSeverity.HIGH, "Bug found", false, 1);
            CodeAnalysisIssue v2Issue = createIssue(2L, "File.java", 11, IssueSeverity.HIGH, "Bug found", false, 2);

            CodeAnalysis v1 = mock(CodeAnalysis.class);
            when(v1.getIssues()).thenReturn(List.of(v1Issue));
            CodeAnalysis v2 = mock(CodeAnalysis.class);
            when(v2.getIssues()).thenReturn(List.of(v2Issue));

            AiAnalysisRequestImpl request = AiAnalysisRequestImpl.builder()
                    .withAllPrAnalysesData(List.of(v2, v1))
                    .build();

            // Should be deduplicated to 1 issue (same fingerprint: same file, same line group, same severity)
            assertThat(request.getPreviousCodeAnalysisIssues()).hasSize(1);
        }

        @Test
        @DisplayName("should preserve resolved status from older version")
        void shouldPreserveResolvedFromOlderVersion() {
            CodeAnalysisIssue v1Resolved = createIssue(1L, "File.java", 10, IssueSeverity.HIGH, "Bug found", true, 1);
            CodeAnalysisIssue v2NotResolved = createIssue(2L, "File.java", 11, IssueSeverity.HIGH, "Bug found", false, 2);

            CodeAnalysis v1 = mock(CodeAnalysis.class);
            when(v1.getIssues()).thenReturn(List.of(v1Resolved));
            CodeAnalysis v2 = mock(CodeAnalysis.class);
            when(v2.getIssues()).thenReturn(List.of(v2NotResolved));

            AiAnalysisRequestImpl request = AiAnalysisRequestImpl.builder()
                    .withAllPrAnalysesData(List.of(v1, v2))
                    .build();

            assertThat(request.getPreviousCodeAnalysisIssues()).hasSize(1);
            assertThat(request.getPreviousCodeAnalysisIssues().get(0).status()).isEqualTo("resolved");
        }

        @Test
        @DisplayName("should prefer resolved when same version")
        void shouldPreferResolvedWhenSameVersion() {
            CodeAnalysisIssue openIssue = createIssue(1L, "File.java", 10, IssueSeverity.HIGH, "Bug found", false, 1);
            CodeAnalysisIssue resolvedIssue = createIssue(2L, "File.java", 11, IssueSeverity.HIGH, "Bug found", true, 1);

            CodeAnalysis analysis = mock(CodeAnalysis.class);
            when(analysis.getIssues()).thenReturn(List.of(openIssue, resolvedIssue));

            AiAnalysisRequestImpl request = AiAnalysisRequestImpl.builder()
                    .withAllPrAnalysesData(List.of(analysis))
                    .build();

            assertThat(request.getPreviousCodeAnalysisIssues()).hasSize(1);
            assertThat(request.getPreviousCodeAnalysisIssues().get(0).status()).isEqualTo("resolved");
        }

        @Test
        @DisplayName("should keep distinct issues with different fingerprints")
        void shouldKeepDistinctIssues() {
            CodeAnalysisIssue issue1 = createIssue(1L, "File.java", 10, IssueSeverity.HIGH, "Bug 1", false, 1);
            CodeAnalysisIssue issue2 = createIssue(2L, "Other.java", 20, IssueSeverity.LOW, "Bug 2", false, 1);

            CodeAnalysis analysis = mock(CodeAnalysis.class);
            when(analysis.getIssues()).thenReturn(List.of(issue1, issue2));

            AiAnalysisRequestImpl request = AiAnalysisRequestImpl.builder()
                    .withAllPrAnalysesData(List.of(analysis))
                    .build();

            assertThat(request.getPreviousCodeAnalysisIssues()).hasSize(2);
        }
    }

    @Nested
    @DisplayName("withEnrichmentData")
    class WithEnrichmentDataTests {
        @Test
        @DisplayName("should set enrichment data")
        void shouldSetEnrichmentData() {
            PrEnrichmentDataDto data = PrEnrichmentDataDto.empty();
            AiAnalysisRequestImpl request = AiAnalysisRequestImpl.builder()
                    .withEnrichmentData(data)
                    .build();
            assertThat(request.getEnrichmentData()).isNotNull();
            assertThat(request.getEnrichmentData().hasData()).isFalse();
        }
    }

    @Nested
    @DisplayName("All getters")
    class AllGettersTests {

        @Test
        @DisplayName("should return all field values correctly")
        void shouldReturnAllFieldValuesCorrectly() {
            AiAnalysisRequestImpl request = AiAnalysisRequestImpl.builder()
                    .withProjectId(99L)
                    .withPullRequestId(42L)
                    .withProjectVcsConnectionBindingInfo("ws", "repo")
                    .withProjectAiConnectionTokenDecrypted("secret-key")
                    .withProjectVcsConnectionCredentials("oauth-client", "oauth-secret")
                    .withAccessToken("token123")
                    .withMaxAllowedTokens(8000)
                    .withUseLocalMcp(false)
                    .withAnalysisType(AnalysisType.BRANCH_ANALYSIS)
                    .withPrTitle("My PR")
                    .withPrDescription("Description")
                    .withChangedFiles(List.of("a.java"))
                    .withDiffSnippets(List.of("diff1"))
                    .withProjectMetadata("workspace", "namespace")
                    .withTargetBranchName("develop")
                    .withVcsProvider("GITHUB")
                    .withRawDiff("full diff")
                    .withAnalysisMode(AnalysisMode.FULL)
                    .withDeltaDiff("delta")
                    .withPreviousCommitHash("prev123")
                    .withCurrentCommitHash("curr456")
                    .build();

            assertThat(request.getProjectId()).isEqualTo(99L);
            assertThat(request.getPullRequestId()).isEqualTo(42L);
            assertThat(request.getProjectVcsWorkspace()).isEqualTo("ws");
            assertThat(request.getProjectVcsRepoSlug()).isEqualTo("repo");
            assertThat(request.getAiApiKey()).isEqualTo("secret-key");
            assertThat(request.getOAuthClient()).isEqualTo("oauth-client");
            assertThat(request.getOAuthSecret()).isEqualTo("oauth-secret");
            assertThat(request.getAccessToken()).isEqualTo("token123");
            assertThat(request.getMaxAllowedTokens()).isEqualTo(8000);
            assertThat(request.getUseLocalMcp()).isFalse();
            assertThat(request.getAnalysisType()).isEqualTo(AnalysisType.BRANCH_ANALYSIS);
            assertThat(request.getPrTitle()).isEqualTo("My PR");
            assertThat(request.getPrDescription()).isEqualTo("Description");
            assertThat(request.getChangedFiles()).containsExactly("a.java");
            assertThat(request.getDiffSnippets()).containsExactly("diff1");
            assertThat(request.getProjectWorkspace()).isEqualTo("workspace");
            assertThat(request.getProjectNamespace()).isEqualTo("namespace");
            assertThat(request.getTargetBranchName()).isEqualTo("develop");
            assertThat(request.getVcsProvider()).isEqualTo("GITHUB");
            assertThat(request.getRawDiff()).isEqualTo("full diff");
            assertThat(request.getAnalysisMode()).isEqualTo(AnalysisMode.FULL);
            assertThat(request.getDeltaDiff()).isEqualTo("delta");
            assertThat(request.getPreviousCommitHash()).isEqualTo("prev123");
            assertThat(request.getCurrentCommitHash()).isEqualTo("curr456");
            assertThat(request.getAiProvider()).isNull();
            assertThat(request.getAiModel()).isNull();
        }
    }
}

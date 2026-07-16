package org.rostilos.codecrow.analysisengine.dto.request.ai;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.FileContentDto;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.ProjectVcsConnectionBinding;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
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
                    .withTaskContext(Map.of("task_key", "PROJ-123", "task_summary", "Build export"))
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
            assertThat(request.getTaskContext()).containsEntry("task_key", "PROJ-123");
            assertThat(request.getTaskContext()).containsEntry("task_summary", "Build export");
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
    void interfaceDefaultsRemainExplicitlyAbsent() {
        AiAnalysisRequest request = mock(
                AiAnalysisRequest.class,
                org.mockito.Answers.CALLS_REAL_METHODS);

        assertThat(request.getProjectWorkspace()).isNull();
        assertThat(request.getProjectNamespace()).isNull();
        assertThat(request.getTargetBranchName()).isNull();
        assertThat(request.getBaseSha()).isNull();
        assertThat(request.getHeadSha()).isNull();
        assertThat(request.getMergeBaseSha()).isNull();
        assertThat(request.getReconciliationFileContents()).isNull();
        assertThat(request.getSourceBranchName()).isNull();
    }

    @Test
    @DisplayName("getPreviousCodeAnalysisIssues should return null when not set")
    void getPreviousCodeAnalysisIssuesShouldReturnNullWhenNotSet() {
        AiAnalysisRequestImpl request = AiAnalysisRequestImpl.builder().build();

        assertThat(request.getPreviousCodeAnalysisIssues()).isNull();
    }

    @Nested
    @DisplayName("Immutable collection snapshot")
    class ImmutableCollectionSnapshotTests {

        @Test
        @DisplayName("should isolate a built request from source, builder, and getter mutation")
        void shouldIsolateBuiltRequestFromMutableCollectionInputs() {
            List<AiRequestPreviousIssueDTO> previousIssues = new ArrayList<>();
            previousIssues.add(previousIssue("issue-1"));

            Map<String, String> taskContext = new LinkedHashMap<>();
            taskContext.put("task_key", "PROJ-123");

            List<String> changedFiles = new ArrayList<>();
            changedFiles.add("src/Main.java");
            changedFiles.add(null); // Legacy requests may contain nullable collection values.

            List<String> deletedFiles = new ArrayList<>();
            deletedFiles.add("src/Old.java");

            List<String> diffSnippets = new ArrayList<>();
            diffSnippets.add("@@ -1 +1 @@");

            Map<String, String> reconciliationFiles = new LinkedHashMap<>();
            reconciliationFiles.put("src/Main.java", "class Main {}");

            var builder = AiAnalysisRequestImpl.builder()
                    .withPreviousIssues(previousIssues)
                    .withTaskContext(taskContext)
                    .withChangedFiles(changedFiles)
                    .withDeletedFiles(deletedFiles)
                    .withDiffSnippets(diffSnippets)
                    .withReconciliationFileContents(reconciliationFiles);

            AiAnalysisRequestImpl request = builder.build();

            previousIssues.add(previousIssue("issue-2"));
            taskContext.put("task_summary", "mutated after build");
            changedFiles.set(0, "src/Mutated.java");
            deletedFiles.clear();
            diffSnippets.add("mutated snippet");
            reconciliationFiles.put("src/Other.java", "class Other {}");

            builder.withPreviousIssues(List.of(previousIssue("builder-replacement")))
                    .withTaskContext(Map.of("task_key", "OTHER-1"))
                    .withChangedFiles(List.of("src/BuilderReplacement.java"))
                    .withDeletedFiles(List.of())
                    .withDiffSnippets(List.of("builder replacement"))
                    .withReconciliationFileContents(Map.of());

            assertThat(request.getPreviousCodeAnalysisIssues())
                    .extracting(AiRequestPreviousIssueDTO::id)
                    .containsExactly("issue-1");
            assertThat(request.getTaskContext())
                    .containsExactly(Map.entry("task_key", "PROJ-123"));
            assertThat(request.getChangedFiles()).containsExactly("src/Main.java", null);
            assertThat(request.getDeletedFiles()).containsExactly("src/Old.java");
            assertThat(request.getDiffSnippets()).containsExactly("@@ -1 +1 @@");
            assertThat(request.getReconciliationFileContents())
                    .containsExactly(Map.entry("src/Main.java", "class Main {}"));

            assertThatThrownBy(() -> request.getPreviousCodeAnalysisIssues().clear())
                    .isInstanceOf(UnsupportedOperationException.class);
            assertThatThrownBy(() -> request.getTaskContext().put("task_key", "mutated"))
                    .isInstanceOf(UnsupportedOperationException.class);
            assertThatThrownBy(() -> request.getChangedFiles().add("src/Injected.java"))
                    .isInstanceOf(UnsupportedOperationException.class);
            assertThatThrownBy(() -> request.getDeletedFiles().clear())
                    .isInstanceOf(UnsupportedOperationException.class);
            assertThatThrownBy(() -> request.getDiffSnippets().set(0, "injected"))
                    .isInstanceOf(UnsupportedOperationException.class);
            assertThatThrownBy(() -> request.getReconciliationFileContents()
                    .put("src/Injected.java", "class Injected {}"))
                    .isInstanceOf(UnsupportedOperationException.class);
        }

        private AiRequestPreviousIssueDTO previousIssue(String id) {
            return new AiRequestPreviousIssueDTO(
                    id,
                    "quality",
                    "high",
                    "Title",
                    "Reason",
                    null,
                    null,
                    "src/Main.java",
                    1,
                    "feature/test",
                    "42",
                    "open",
                    "CODE_QUALITY",
                    1,
                    null,
                    null,
                    null,
                    "line");
        }
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
        void shouldReplaceAnOlderOpenIssueWithANewerOpenIssue() {
            CodeAnalysisIssue older = createIssue(
                    1L, "File.java", 10, IssueSeverity.HIGH, "Bug found", false, 1);
            CodeAnalysisIssue newer = createIssue(
                    2L, "File.java", 11, IssueSeverity.HIGH, "Bug found", false, 2);
            CodeAnalysis oldAnalysis = mock(CodeAnalysis.class);
            CodeAnalysis newAnalysis = mock(CodeAnalysis.class);
            when(oldAnalysis.getIssues()).thenReturn(List.of(older));
            when(newAnalysis.getIssues()).thenReturn(List.of(newer));

            AiAnalysisRequestImpl request = AiAnalysisRequestImpl.builder()
                    .withAllPrAnalysesData(List.of(oldAnalysis, newAnalysis))
                    .build();

            assertThat(request.getPreviousCodeAnalysisIssues()).hasSize(1);
            assertThat(request.getPreviousCodeAnalysisIssues().get(0).prVersion()).isEqualTo(2);
        }

        @Test
        void nullableIssueFieldsStillProduceADeterministicFingerprint() {
            CodeAnalysisIssue issue = new CodeAnalysisIssue();
            CodeAnalysis versionlessAnalysis = new CodeAnalysis();
            issue.setAnalysis(versionlessAnalysis);
            CodeAnalysis analysis = mock(CodeAnalysis.class);
            when(analysis.getIssues()).thenReturn(List.of(issue));

            AiAnalysisRequestImpl request = AiAnalysisRequestImpl.builder()
                    .withAllPrAnalysesData(List.of(analysis))
                    .build();

            assertThat(request.getPreviousCodeAnalysisIssues()).hasSize(1);
        }

        @Test
        void nullableVersionsAndResolvedDuplicatesCoverEveryTieBreakDirection() {
            CodeAnalysisIssue versionlessExisting = createIssue(
                    1L, "VersionlessExisting.java", 10, IssueSeverity.HIGH,
                    "same", false, 1);
            versionlessExisting.getAnalysis().setPrVersion(null);
            CodeAnalysisIssue newerAfterVersionless = createIssue(
                    2L, "VersionlessExisting.java", 11, IssueSeverity.HIGH,
                    "same", false, 2);

            CodeAnalysisIssue versionedExisting = createIssue(
                    3L, "VersionlessCurrent.java", 10, IssueSeverity.HIGH,
                    "same", false, 1);
            CodeAnalysisIssue versionlessCurrent = createIssue(
                    4L, "VersionlessCurrent.java", 11, IssueSeverity.HIGH,
                    "same", false, 1);
            versionlessCurrent.getAnalysis().setPrVersion(null);

            CodeAnalysisIssue olderResolved = createIssue(
                    5L, "BothResolved.java", 10, IssueSeverity.HIGH,
                    "same", true, 1);
            CodeAnalysisIssue newerResolved = createIssue(
                    6L, "BothResolved.java", 11, IssueSeverity.HIGH,
                    "same", true, 2);

            CodeAnalysisIssue sameVersionOpen = createIssue(
                    7L, "OpenTie.java", 10, IssueSeverity.HIGH,
                    "same", false, 1);
            CodeAnalysisIssue anotherOpen = createIssue(
                    8L, "OpenTie.java", 11, IssueSeverity.HIGH,
                    "same", false, 1);

            CodeAnalysisIssue sameVersionResolved = createIssue(
                    9L, "ResolvedTie.java", 10, IssueSeverity.HIGH,
                    "same", true, 1);
            CodeAnalysisIssue anotherResolved = createIssue(
                    10L, "ResolvedTie.java", 11, IssueSeverity.HIGH,
                    "same", true, 1);

            CodeAnalysis analysis = mock(CodeAnalysis.class);
            when(analysis.getIssues()).thenReturn(List.of(
                    versionlessExisting, newerAfterVersionless,
                    versionedExisting, versionlessCurrent,
                    olderResolved, newerResolved,
                    sameVersionOpen, anotherOpen,
                    sameVersionResolved, anotherResolved));

            AiAnalysisRequestImpl request = AiAnalysisRequestImpl.builder()
                    .withAllPrAnalysesData(List.of(analysis))
                    .build();

            assertThat(request.getPreviousCodeAnalysisIssues()).hasSize(5);
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

        @Test
        @DisplayName("should keep the built enrichment snapshot isolated")
        void shouldKeepBuiltEnrichmentSnapshotIsolated() {
            List<FileContentDto> sourceFiles = new ArrayList<>();
            sourceFiles.add(FileContentDto.of("src/Main.java", "class Main {}"));
            PrEnrichmentDataDto enrichment = new PrEnrichmentDataDto(
                    sourceFiles,
                    List.of(),
                    List.of(),
                    PrEnrichmentDataDto.EnrichmentStats.empty());
            var builder = AiAnalysisRequestImpl.builder().withEnrichmentData(enrichment);

            AiAnalysisRequestImpl request = builder.build();

            sourceFiles.clear();
            builder.withEnrichmentData(PrEnrichmentDataDto.empty());

            assertThat(request.getEnrichmentData().fileContents())
                    .extracting(FileContentDto::path)
                    .containsExactly("src/Main.java");
            assertThatThrownBy(() -> request.getEnrichmentData().fileContents().clear())
                    .isInstanceOf(UnsupportedOperationException.class);
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
                    .withAiCustomParameters("{\"temperature\":0.2}")
                    .withProjectVcsConnectionCredentials("oauth-client", "oauth-secret")
                    .withAccessToken("token123")
                    .withMaxAllowedTokens(8000)
                    .withUseLocalMcp(false)
                    .withAnalysisType(AnalysisType.BRANCH_ANALYSIS)
                    .withPrTitle("My PR")
                    .withPrDescription("Description")
                    .withTaskContext(Map.of("task_key", "PROJ-456"))
                    .withTaskHistoryContext("Prior PR #10 covered AC1")
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
            assertThat(request.getAiCustomParameters()).isEqualTo("{\"temperature\":0.2}");
            assertThat(request.getOAuthClient()).isEqualTo("oauth-client");
            assertThat(request.getOAuthSecret()).isEqualTo("oauth-secret");
            assertThat(request.getAccessToken()).isEqualTo("token123");
            assertThat(request.getMaxAllowedTokens()).isEqualTo(8000);
            assertThat(request.getUseLocalMcp()).isFalse();
            assertThat(request.getAnalysisType()).isEqualTo(AnalysisType.BRANCH_ANALYSIS);
            assertThat(request.getPrTitle()).isEqualTo("My PR");
            assertThat(request.getPrDescription()).isEqualTo("Description");
            assertThat(request.getTaskContext()).containsEntry("task_key", "PROJ-456");
            assertThat(request.getTaskHistoryContext()).isEqualTo("Prior PR #10 covered AC1");
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

    @Nested
    @DisplayName("sourceBranchName")
    class SourceBranchNameTests {

        @Test
        @DisplayName("should set and get sourceBranchName via builder")
        void shouldSetAndGetSourceBranchName() {
            AiAnalysisRequestImpl request = AiAnalysisRequestImpl.builder()
                    .withProjectId(1L)
                    .withSourceBranchName("feature/my-branch")
                    .build();

            assertThat(request.getSourceBranchName()).isEqualTo("feature/my-branch");
        }

        @Test
        @DisplayName("should default sourceBranchName to null")
        void shouldDefaultSourceBranchNameToNull() {
            AiAnalysisRequestImpl request = AiAnalysisRequestImpl.builder()
                    .withProjectId(1L)
                    .build();

            assertThat(request.getSourceBranchName()).isNull();
        }

        @Test
        @DisplayName("should include sourceBranchName alongside targetBranchName")
        void shouldIncludeSourceBranchNameAlongsideTarget() {
            AiAnalysisRequestImpl request = AiAnalysisRequestImpl.builder()
                    .withProjectId(1L)
                    .withSourceBranchName("feature/xyz")
                    .withTargetBranchName("main")
                    .build();

            assertThat(request.getSourceBranchName()).isEqualTo("feature/xyz");
            assertThat(request.getTargetBranchName()).isEqualTo("main");
        }
    }
}

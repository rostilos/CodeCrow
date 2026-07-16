package org.rostilos.codecrow.core.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.InOrder;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.qualitygate.QualityGateRepository;
import org.rostilos.codecrow.core.service.qualitygate.QualityGateEvaluator;

class CodeAnalysisCandidatePersistenceTest {
    private static final String HEAD_SHA = "b".repeat(64);
    private static final String EXECUTION_ID = "candidate-pr-42";
    private static final String MANIFEST_DIGEST = "d".repeat(64);

    private CodeAnalysisRepository repository;
    private CodeAnalysisIssueRepository issueRepository;
    private IssueDeduplicationService issueDeduplicationService;
    private CodeAnalysisService service;
    private Project project;

    @BeforeEach
    void setUp() {
        repository = mock(CodeAnalysisRepository.class);
        issueRepository = mock(CodeAnalysisIssueRepository.class);
        project = mock(Project.class);
        when(project.getId()).thenReturn(7L);
        when(repository.lockExecutionManifest(EXECUTION_ID))
                .thenReturn(Optional.of(EXECUTION_ID));
        issueDeduplicationService = mock(IssueDeduplicationService.class);
        when(issueDeduplicationService.deduplicateAtIngestion(any()))
                .thenAnswer(invocation -> invocation.getArgument(0));
        service = new CodeAnalysisService(
                repository,
                issueRepository,
                mock(QualityGateRepository.class),
                mock(QualityGateEvaluator.class),
                issueDeduplicationService);
    }

    @Test
    void candidateWritePersistsManifestIdentityOnTheFirstAnalysisSave() {
        when(repository.findByExecutionId(EXECUTION_ID)).thenReturn(Optional.empty());
        when(repository.findByProjectIdAndCommitHashAndPrNumber(7L, HEAD_SHA, 42L))
                .thenReturn(Optional.empty());
        when(repository.findMaxPrVersion(7L, 42L)).thenReturn(Optional.of(2));
        when(repository.save(any(CodeAnalysis.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        CodeAnalysis result = createCandidate(EXECUTION_ID, MANIFEST_DIGEST);

        assertThat(result.getExecutionId()).isEqualTo(EXECUTION_ID);
        assertThat(result.getArtifactManifestDigest()).isEqualTo(MANIFEST_DIGEST);
        assertThat(result.getProject()).isSameAs(project);
        assertThat(result.getPrNumber()).isEqualTo(42L);
        assertThat(result.getCommitHash()).isEqualTo(HEAD_SHA);
        assertThat(result.getPrVersion()).isEqualTo(3);
        verify(repository).save(result);
    }

    @Test
    void candidateAcceptsNullFileInventoryAndShortTaskMetadata() {
        when(repository.findByExecutionId(EXECUTION_ID)).thenReturn(Optional.empty());
        when(repository.findByProjectIdAndCommitHashAndPrNumber(7L, HEAD_SHA, 42L))
                .thenReturn(Optional.empty());
        when(repository.findMaxPrVersion(7L, 42L)).thenReturn(Optional.empty());
        when(repository.save(any(CodeAnalysis.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        CodeAnalysis result = service.createCandidateAnalysisFromAiResponse(
                project,
                Map.of("comment", "review"),
                42L,
                "main",
                "feature",
                HEAD_SHA,
                null,
                null,
                null,
                null,
                " task-key ",
                "summary",
                EXECUTION_ID,
                MANIFEST_DIGEST);

        assertThat(result.getTaskId()).isEqualTo("task-key");
        assertThat(result.getTaskSummary()).isEqualTo("summary");
    }

    @Test
    void exactExecutionRetryReturnsOnlyTheAlreadyBoundOutput() {
        CodeAnalysis existing = boundAnalysis(MANIFEST_DIGEST);
        when(repository.findByExecutionId(EXECUTION_ID))
                .thenReturn(Optional.of(existing));

        assertThat(createCandidate(EXECUTION_ID, MANIFEST_DIGEST))
                .isSameAs(existing);
        verify(repository, never())
                .findByProjectIdAndCommitHashAndPrNumber(any(), any(), any());
        verify(repository, never()).save(any());
    }

    @Test
    void candidateSerializesOutputCreationOnTheDurableManifestRow() {
        CodeAnalysis existing = boundAnalysis(MANIFEST_DIGEST);
        when(repository.findByExecutionId(EXECUTION_ID))
                .thenReturn(Optional.of(existing));

        assertThat(createCandidate(EXECUTION_ID, MANIFEST_DIGEST))
                .isSameAs(existing);

        InOrder ordered = org.mockito.Mockito.inOrder(repository);
        ordered.verify(repository).lockExecutionManifest(EXECUTION_ID);
        ordered.verify(repository).findByExecutionId(EXECUTION_ID);
    }

    @Test
    void candidateRejectsOutputWhenItsDurableManifestRowIsMissing() {
        when(repository.lockExecutionManifest(EXECUTION_ID))
                .thenReturn(Optional.empty());

        assertThatThrownBy(() -> createCandidate(EXECUTION_ID, MANIFEST_DIGEST))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("manifest")
                .hasMessageContaining("missing");

        verify(repository, never()).findByExecutionId(any());
        verify(repository, never()).save(any());
    }

    @Test
    void candidateRejectsAConflictingManifestLockBeforeOutputLookup() {
        when(repository.lockExecutionManifest(EXECUTION_ID))
                .thenReturn(Optional.of("another-execution"));

        assertThatThrownBy(() -> createCandidate(EXECUTION_ID, MANIFEST_DIGEST))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("manifest")
                .hasMessageContaining("conflicting");

        verify(repository, never()).findByExecutionId(any());
        verify(repository, never()).save(any());
    }

    @Test
    void executionRetryRejectsAChangedManifestDigest() {
        when(repository.findByExecutionId(EXECUTION_ID))
                .thenReturn(Optional.of(boundAnalysis("a".repeat(64))));

        assertThatThrownBy(() -> createCandidate(EXECUTION_ID, MANIFEST_DIGEST))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("conflicts with immutable execution identity");
        verify(repository, never()).save(any());
    }

    @Test
    void candidateRecomputesBesideAnUnboundLegacyRowWithoutMutatingHistory() {
        CodeAnalysis legacy = new CodeAnalysis();
        legacy.setProject(project);
        legacy.setAnalysisType(AnalysisType.PR_REVIEW);
        legacy.setPrNumber(42L);
        legacy.setPrVersion(3);
        legacy.setCommitHash(HEAD_SHA);
        legacy.setComment("preserved legacy history");
        when(repository.findByExecutionId(EXECUTION_ID)).thenReturn(Optional.empty());
        when(repository.findByProjectIdAndCommitHashAndPrNumber(7L, HEAD_SHA, 42L))
                .thenReturn(Optional.of(legacy));
        when(repository.findMaxPrVersion(7L, 42L)).thenReturn(Optional.of(3));
        when(repository.save(any(CodeAnalysis.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        CodeAnalysis recomputed = createCandidate(EXECUTION_ID, MANIFEST_DIGEST);

        assertThat(recomputed).isNotSameAs(legacy);
        assertThat(recomputed.getExecutionId()).isEqualTo(EXECUTION_ID);
        assertThat(recomputed.getArtifactManifestDigest()).isEqualTo(MANIFEST_DIGEST);
        assertThat(recomputed.getPrVersion()).isEqualTo(4);
        assertThat(legacy.hasExecutionIdentity()).isFalse();
        assertThat(legacy.getComment()).isEqualTo("preserved legacy history");
        assertThat(legacy.getPrVersion()).isEqualTo(3);
        verify(repository).save(recomputed);
    }

    @Test
    void changedExecutionIdentityPersistsBesidePriorCandidateAtTheSameHead() {
        CodeAnalysis priorExecution = new CodeAnalysis();
        priorExecution.setProject(project);
        priorExecution.setAnalysisType(AnalysisType.PR_REVIEW);
        priorExecution.setPrNumber(42L);
        priorExecution.setCommitHash(HEAD_SHA);
        priorExecution.bindExecutionIdentity(
                "candidate-pr-42-prior-policy",
                "c".repeat(64));
        when(repository.findByExecutionId(EXECUTION_ID)).thenReturn(Optional.empty());
        when(repository.findByProjectIdAndCommitHashAndPrNumber(7L, HEAD_SHA, 42L))
                .thenReturn(Optional.of(priorExecution));
        when(repository.findMaxPrVersion(7L, 42L)).thenReturn(Optional.of(3));
        when(repository.save(any(CodeAnalysis.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        CodeAnalysis recomputed = createCandidate(EXECUTION_ID, MANIFEST_DIGEST);

        assertThat(recomputed).isNotSameAs(priorExecution);
        assertThat(recomputed.getExecutionId()).isEqualTo(EXECUTION_ID);
        assertThat(recomputed.getArtifactManifestDigest()).isEqualTo(MANIFEST_DIGEST);
        assertThat(recomputed.getPrVersion()).isEqualTo(4);
        verify(repository).save(recomputed);
    }

    @Test
    void missingCandidateIdentityFailsBeforeAnyRepositoryAccess() {
        assertThatThrownBy(() -> createCandidate(null, MANIFEST_DIGEST))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("executionId");
        verify(repository, never()).findByExecutionId(any());
        verify(repository, never()).save(any());
    }

    @Test
    void candidateIgnoresProducerIssueIdsWithoutImportingLegacyState() {
        when(repository.findByExecutionId(EXECUTION_ID)).thenReturn(Optional.empty());
        when(repository.findByProjectIdAndCommitHashAndPrNumber(7L, HEAD_SHA, 42L))
                .thenReturn(Optional.empty());
        when(repository.findMaxPrVersion(7L, 42L)).thenReturn(Optional.empty());
        when(repository.save(any(CodeAnalysis.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        Map<String, Object> numericProducerIssue = new HashMap<>();
        numericProducerIssue.put("id", "91");
        numericProducerIssue.put("severity", "MEDIUM");
        numericProducerIssue.put("category", "BUG_RISK");
        numericProducerIssue.put("file", "src/App.java");
        numericProducerIssue.put("line", 5);
        numericProducerIssue.put("scope", "LINE");
        numericProducerIssue.put("title", "Risky call remains");
        numericProducerIssue.put("reason", "A risky call remains unguarded.");

        for (Object issues : List.of(
                List.of(numericProducerIssue),
                Map.of("0", numericProducerIssue))) {
            CodeAnalysis result = createCandidate(
                    EXECUTION_ID,
                    MANIFEST_DIGEST,
                    Map.of("comment", "review", "issues", issues));
            assertThat(result.getIssues()).singleElement().satisfies(issue ->
                    assertThat(issue.getTitle()).isEqualTo("Risky call remains"));
        }

        verify(issueRepository, never()).findById(any());
    }

    @Test
    void executionBoundCandidatePreservesFindingOrderWithoutLegacyIngestionDedup() {
        when(repository.findByExecutionId(EXECUTION_ID)).thenReturn(Optional.empty());
        when(repository.findByProjectIdAndCommitHashAndPrNumber(7L, HEAD_SHA, 42L))
                .thenReturn(Optional.empty());
        when(repository.findMaxPrVersion(7L, 42L)).thenReturn(Optional.empty());
        when(repository.save(any(CodeAnalysis.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        Map<String, Object> first = candidateIssue(
                "First distinct candidate", "First distinct root cause");
        Map<String, Object> second = candidateIssue(
                "Second distinct candidate", "Second distinct root cause");

        CodeAnalysis result = createCandidate(
                EXECUTION_ID,
                MANIFEST_DIGEST,
                Map.of("comment", "review", "issues", List.of(first, second)));

        assertThat(result.getIssues())
                .extracting(issue -> issue.getTitle())
                .containsExactly(
                        "First distinct candidate",
                        "Second distinct candidate");
        verify(issueDeduplicationService, never()).deduplicateAtIngestion(any());
    }

    @Test
    void candidateRejectsMissingResponseDataBeforeAnyRepositoryAccess() {
        assertThatThrownBy(() -> createCandidate(
                EXECUTION_ID, MANIFEST_DIGEST, null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("response data");

        verify(repository, never()).findByExecutionId(any());
        verify(repository, never()).save(any());
    }

    @Test
    void candidateRejectsEveryUnpersistedCoordinateBeforeManifestLocking() {
        Project missingId = mock(Project.class);
        when(missingId.getId()).thenReturn(null);
        Project zeroId = mock(Project.class);
        when(zeroId.getId()).thenReturn(0L);

        for (Project invalid : java.util.Arrays.asList(null, missingId, zeroId)) {
            assertThatThrownBy(() -> service.createCandidateAnalysisFromAiResponse(
                    invalid, Map.of("comment", "review"), 42L,
                    "main", "feature", HEAD_SHA, null, null,
                    null, Map.of(), null, null, EXECUTION_ID, MANIFEST_DIGEST))
                    .isInstanceOf(IllegalArgumentException.class)
                    .hasMessageContaining("persisted project");
        }
        for (Long invalidPr : java.util.Arrays.asList(null, 0L)) {
            assertThatThrownBy(() -> service.createCandidateAnalysisFromAiResponse(
                    project, Map.of("comment", "review"), invalidPr,
                    "main", "feature", HEAD_SHA, null, null,
                    null, Map.of(), null, null, EXECUTION_ID, MANIFEST_DIGEST))
                    .isInstanceOf(IllegalArgumentException.class)
                    .hasMessageContaining("pull-request ID");
        }
        for (String invalidSha : java.util.Arrays.asList(null, "abc", "B".repeat(40))) {
            assertThatThrownBy(() -> service.createCandidateAnalysisFromAiResponse(
                    project, Map.of("comment", "review"), 42L,
                    "main", "feature", invalidSha, null, null,
                    null, Map.of(), null, null, EXECUTION_ID, MANIFEST_DIGEST))
                    .isInstanceOf(IllegalArgumentException.class)
                    .hasMessageContaining("exact head SHA");
        }
    }

    @Test
    void candidateMapIssuesWithoutIdentityAreAcceptedOnRetry() {
        CodeAnalysis existing = boundAnalysis(MANIFEST_DIGEST);
        when(repository.findByExecutionId(EXECUTION_ID)).thenReturn(Optional.of(existing));

        assertThat(createCandidate(
                EXECUTION_ID,
                MANIFEST_DIGEST,
                Map.of("issues", Map.of("first", Map.of("severity", "HIGH")))))
                .isSameAs(existing);
    }

    @Test
    void executionRetryRejectsMissingProjectAndEveryChangedCoordinate() {
        CodeAnalysis noProject = boundAnalysis(MANIFEST_DIGEST);
        noProject.setProject(null);
        when(repository.findByExecutionId(EXECUTION_ID)).thenReturn(Optional.of(noProject));
        assertThatThrownBy(() -> createCandidate(EXECUTION_ID, MANIFEST_DIGEST))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("immutable execution identity");

        CodeAnalysis wrongPr = boundAnalysis(MANIFEST_DIGEST);
        wrongPr.setPrNumber(41L);
        when(repository.findByExecutionId(EXECUTION_ID)).thenReturn(Optional.of(wrongPr));
        assertThatThrownBy(() -> createCandidate(EXECUTION_ID, MANIFEST_DIGEST))
                .isInstanceOf(IllegalStateException.class);

        CodeAnalysis wrongHead = boundAnalysis(MANIFEST_DIGEST);
        wrongHead.setCommitHash("c".repeat(64));
        when(repository.findByExecutionId(EXECUTION_ID)).thenReturn(Optional.of(wrongHead));
        assertThatThrownBy(() -> createCandidate(EXECUTION_ID, MANIFEST_DIGEST))
                .isInstanceOf(IllegalStateException.class);

        CodeAnalysis wrongType = boundAnalysis(MANIFEST_DIGEST);
        wrongType.setAnalysisType(AnalysisType.BRANCH_ANALYSIS);
        when(repository.findByExecutionId(EXECUTION_ID)).thenReturn(Optional.of(wrongType));
        assertThatThrownBy(() -> createCandidate(EXECUTION_ID, MANIFEST_DIGEST))
                .isInstanceOf(IllegalStateException.class);
    }

    @Test
    void executionRetryRejectsMissingIdentityAndChangedExecutionId() {
        when(repository.findByExecutionId(EXECUTION_ID))
                .thenReturn(Optional.of(new CodeAnalysis()));
        assertThatThrownBy(() -> createCandidate(EXECUTION_ID, MANIFEST_DIGEST))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("immutable execution identity");

        CodeAnalysis wrongExecution = new CodeAnalysis();
        wrongExecution.setProject(project);
        wrongExecution.setAnalysisType(AnalysisType.PR_REVIEW);
        wrongExecution.setPrNumber(42L);
        wrongExecution.setCommitHash(HEAD_SHA);
        wrongExecution.bindExecutionIdentity("another-execution", MANIFEST_DIGEST);
        when(repository.findByExecutionId(EXECUTION_ID))
                .thenReturn(Optional.of(wrongExecution));

        assertThatThrownBy(() -> createCandidate(EXECUTION_ID, MANIFEST_DIGEST))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("immutable execution identity");
    }

    @Test
    void candidateRetryAllowsOnlyAbsentIssueIdentity() {
        CodeAnalysis existing = boundAnalysis(MANIFEST_DIGEST);
        when(repository.findByExecutionId(EXECUTION_ID))
                .thenReturn(Optional.of(existing));
        Map<String, Object> issueWithoutIdentity = new HashMap<>();
        issueWithoutIdentity.put("id", null);
        Map<String, Object> response = new HashMap<>();
        response.put("issues", List.of("not-an-issue-map", issueWithoutIdentity));

        assertThat(createCandidate(EXECUTION_ID, MANIFEST_DIGEST, response))
                .isSameAs(existing);
        verify(issueRepository, never()).findById(any());
    }

    @Test
    void legacyPersistenceRemainsExplicitlyUnbound() {
        when(repository.findByProjectIdAndCommitHashAndPrNumber(7L, "legacy", 42L))
                .thenReturn(Optional.empty());
        when(repository.findMaxPrVersion(7L, 42L)).thenReturn(Optional.empty());
        when(repository.save(any(CodeAnalysis.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        CodeAnalysis legacy = service.createAnalysisFromAiResponse(
                project,
                Map.of("comment", "legacy"),
                42L,
                "main",
                "feature",
                "legacy",
                null,
                null);

        assertThat(legacy.hasExecutionIdentity()).isFalse();
        assertThat(legacy.getExecutionId()).isNull();
        assertThat(legacy.getArtifactManifestDigest()).isNull();
    }

    private CodeAnalysis createCandidate(String executionId, String manifestDigest) {
        return createCandidate(
                executionId,
                manifestDigest,
                Map.of("comment", "review"));
    }

    private CodeAnalysis createCandidate(
            String executionId,
            String manifestDigest,
            Map<String, Object> analysisData) {
        return service.createCandidateAnalysisFromAiResponse(
                project,
                analysisData,
                42L,
                "main",
                "feature",
                HEAD_SHA,
                "author-id",
                "author",
                "f".repeat(64),
                Map.of(),
                null,
                null,
                executionId,
                manifestDigest);
    }

    private CodeAnalysis boundAnalysis(String manifestDigest) {
        CodeAnalysis analysis = new CodeAnalysis();
        analysis.setProject(project);
        analysis.setAnalysisType(AnalysisType.PR_REVIEW);
        analysis.setPrNumber(42L);
        analysis.setCommitHash(HEAD_SHA);
        analysis.bindExecutionIdentity(EXECUTION_ID, manifestDigest);
        return analysis;
    }

    private static Map<String, Object> candidateIssue(
            String title,
            String reason) {
        Map<String, Object> issue = new HashMap<>();
        issue.put("severity", "MEDIUM");
        issue.put("category", "BUG_RISK");
        issue.put("file", "src/App.java");
        issue.put("line", 5);
        issue.put("scope", "LINE");
        issue.put("title", title);
        issue.put("reason", reason);
        return issue;
    }
}

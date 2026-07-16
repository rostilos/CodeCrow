package org.rostilos.codecrow.core.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisStatus;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisResult;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.DetectionSource;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueScope;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.qualitygate.QualityGate;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateResult;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.qualitygate.QualityGateRepository;
import org.rostilos.codecrow.core.service.qualitygate.QualityGateEvaluator;
import org.rostilos.codecrow.core.util.tracking.LineHashSequence;
import org.springframework.test.util.ReflectionTestUtils;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.reset;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class CodeAnalysisServiceCoverageGapsTest {
    @Mock CodeAnalysisRepository repository;
    @Mock CodeAnalysisIssueRepository issueRepository;
    @Mock QualityGateRepository qualityGateRepository;
    @Mock QualityGateEvaluator qualityGateEvaluator;

    private CodeAnalysisService service;
    private Project project;

    @BeforeEach
    void setUp() {
        service = new CodeAnalysisService(
                repository,
                issueRepository,
                qualityGateRepository,
                qualityGateEvaluator,
                new IssueDeduplicationService());
        project = new Project();
        ReflectionTestUtils.setField(project, "id", 7L);
        project.setName("coverage");
    }

    @Test
    void directPushCreationCoversIdempotencyPersistenceTaggingAndFailure() {
        CodeAnalysis existing = new CodeAnalysis();
        when(repository.findByProjectIdAndCommitHashAndAnalysisType(
                7L, "existing", AnalysisType.BRANCH_ANALYSIS))
                .thenReturn(Optional.of(existing));
        assertThat(service.createDirectPushAnalysisFromAiResponse(
                project, Map.of("comment", "cached"), "main", "existing", Map.of()))
                .isSameAs(existing);

        when(repository.findByProjectIdAndCommitHashAndAnalysisType(
                7L, "head", AnalysisType.BRANCH_ANALYSIS))
                .thenReturn(Optional.empty());
        when(repository.save(any(CodeAnalysis.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        Map<String, Object> issue = basicIssue();
        CodeAnalysis created = service.createDirectPushAnalysisFromAiResponse(
                project,
                Map.of("comment", "review", "issues", List.of(issue)),
                "main",
                "head",
                null);

        assertThat(created.getAnalysisType()).isEqualTo(AnalysisType.BRANCH_ANALYSIS);
        assertThat(created.getPrNumber()).isNull();
        assertThat(created.getSourceBranchName()).isNull();
        assertThat(created.getPrVersion()).isZero();
        assertThat(created.getIssues()).singleElement()
                .extracting(CodeAnalysisIssue::getDetectionSource)
                .isEqualTo(DetectionSource.DIRECT_PUSH_ANALYSIS);

        when(repository.findByProjectIdAndCommitHashAndAnalysisType(
                7L, "head-map", AnalysisType.BRANCH_ANALYSIS))
                .thenReturn(Optional.empty());
        CodeAnalysis withMaterializedFileMap = service.createDirectPushAnalysisFromAiResponse(
                project,
                Map.of("comment", "review"),
                "main",
                "head-map",
                Map.of());
        assertThat(withMaterializedFileMap.getIssues()).isEmpty();

        when(repository.findByProjectIdAndCommitHashAndAnalysisType(
                7L, "broken", AnalysisType.BRANCH_ANALYSIS))
                .thenThrow(new IllegalStateException("repository unavailable"));
        assertThatThrownBy(() -> service.createDirectPushAnalysisFromAiResponse(
                project, Map.of("comment", "review"), "main", "broken", Map.of()))
                .isInstanceOf(RuntimeException.class)
                .hasMessageContaining("direct push analysis")
                .hasCauseInstanceOf(IllegalStateException.class);
    }

    @Test
    void legacyCreationNormalizesTaskMetadataAndWrapsRepositoryFailure() {
        when(repository.findByProjectIdAndCommitHashAndPrNumber(7L, "head", 42L))
                .thenReturn(Optional.empty());
        when(repository.findMaxPrVersion(7L, 42L)).thenReturn(Optional.empty());
        when(repository.save(any(CodeAnalysis.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        CodeAnalysis analysis = service.createAnalysisFromAiResponse(
                project,
                Map.of("comment", "review"),
                42L,
                "main",
                "feature",
                "head",
                null,
                null,
                null,
                null,
                "  " + "T".repeat(140) + "  ",
                "   ");

        assertThat(analysis.getTaskId()).hasSize(128).doesNotStartWith(" ");
        assertThat(analysis.getTaskSummary()).isNull();

        when(repository.findByProjectIdAndCommitHashAndPrNumber(7L, "failure", 42L))
                .thenThrow(new IllegalStateException("read failed"));
        assertThatThrownBy(() -> service.createAnalysisFromAiResponse(
                project, Map.of("comment", "review"), 42L,
                "main", "feature", "failure", null, null))
                .isInstanceOf(RuntimeException.class)
                .hasMessageContaining("Failed to create analysis")
                .hasCauseInstanceOf(IllegalStateException.class);
    }

    @Test
    void fillAnalysisToleratesMalformedIssueContainersAndQualityGateFailure() {
        QualityGate gate = new QualityGate();
        gate.setActive(true);
        gate.setName("strict");
        project.setQualityGate(gate);
        when(repository.findByProjectIdAndCommitHashAndPrNumber(
                any(), any(), any())).thenReturn(Optional.empty());
        when(repository.findMaxPrVersion(any(), any())).thenReturn(Optional.empty());
        when(repository.save(any(CodeAnalysis.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(qualityGateEvaluator.evaluate(any(), any()))
                .thenThrow(new IllegalStateException("detached gate"));

        List<Object> listIssues = new ArrayList<>();
        listIssues.add("not a map");
        listIssues.add(issueWithEdgeValues());
        Map<String, Object> missingLocation = basicIssue();
        missingLocation.remove("line");
        missingLocation.remove("reason");
        missingLocation.remove("category");
        missingLocation.put("file", "B.java");
        missingLocation.put("title", "X".repeat(300));
        missingLocation.put("scope", "FILE");
        listIssues.add(missingLocation);
        listIssues.add(Map.of("severity", 17));
        CodeAnalysis fromList = service.createAnalysisFromAiResponse(
                project,
                Map.of("comment", "review", "issues", listIssues),
                42L, "main", "feature", "list", null, null);
        assertThat(fromList.getIssues()).hasSize(2);
        CodeAnalysisIssue edge = fromList.getIssues().get(0);
        assertThat(edge.getReason()).isEqualTo("First sentence. Details\0removed".replace("\0", ""));
        assertThat(edge.getTitle()).isEqualTo("First sentence");
        assertThat(edge.getLineNumber()).isOne();
        assertThat(edge.getIssueScope()).isEqualTo(IssueScope.FILE);
        assertThat(edge.getIssueCategory()).isEqualTo(IssueCategory.CODE_QUALITY);
        CodeAnalysisIssue missing = fromList.getIssues().get(1);
        assertThat(missing.getLineNumber()).isOne();
        assertThat(missing.getReason()).isEqualTo("No reason provided");
        assertThat(missing.getTitle()).hasSize(255).endsWith("...");
        assertThat(missing.getIssueCategory()).isEqualTo(IssueCategory.CODE_QUALITY);

        Map<String, Object> mapIssues = new LinkedHashMap<>();
        mapIssues.put("null", null);
        mapIssues.put("wrong", "not a map");
        mapIssues.put("valid", basicIssue());
        CodeAnalysis fromMap = service.createAnalysisFromAiResponse(
                project,
                Map.of("comment", "review", "issues", mapIssues),
                43L, "main", "feature", "map", null, null);
        assertThat(fromMap.getIssues()).hasSize(1);
    }

    @Test
    void fillAnalysisWrapsSaveFailuresAndPrivateQualityGateBranchesAreSafe()
            throws Throwable {
        CodeAnalysis noProject = new CodeAnalysis();
        assertThat(invoke("getQualityGateForAnalysis",
                new Class<?>[]{CodeAnalysis.class}, noProject)).isNull();
        CodeAnalysis noWorkspace = new CodeAnalysis();
        noWorkspace.setProject(project);
        assertThat(invoke("getQualityGateForAnalysis",
                new Class<?>[]{CodeAnalysis.class}, noWorkspace)).isNull();

        when(repository.findByProjectIdAndCommitHashAndPrNumber(7L, "save-failure", 42L))
                .thenReturn(Optional.empty());
        when(repository.findMaxPrVersion(7L, 42L)).thenReturn(Optional.empty());
        when(repository.save(any(CodeAnalysis.class)))
                .thenThrow(new IllegalStateException("write failed"));
        assertThatThrownBy(() -> service.createAnalysisFromAiResponse(
                project,
                Map.of("comment", "review", "issues", List.of()),
                42L, "main", "feature", "save-failure", null, null))
                .isInstanceOf(RuntimeException.class)
                .hasMessageContaining("Failed to create analysis");
    }

    @Test
    void previousAnalysisRemovalCoversSuccessAndFailure() throws Throwable {
        CodeAnalysis analysis = new CodeAnalysis();
        analysis.addIssue(new CodeAnalysisIssue());
        when(repository.save(analysis)).thenReturn(analysis);

        assertThat(invoke("removePreviousAnalysisData",
                new Class<?>[]{CodeAnalysis.class}, analysis)).isSameAs(analysis);
        assertThat(analysis.getIssues()).isEmpty();

        when(repository.save(analysis)).thenThrow(new IllegalStateException("write failed"));
        assertThatThrownBy(() -> invoke(
                "removePreviousAnalysisData", new Class<?>[]{CodeAnalysis.class}, analysis))
                .isInstanceOf(InvocationTargetException.class)
                .hasCauseInstanceOf(RuntimeException.class)
                .cause()
                .hasMessageContaining("remove previous analysis");
    }

    @Test
    void issueHelperCoversBackwardOverloadAndTrackingFailure() throws Throwable {
        @SuppressWarnings("unchecked")
        CodeAnalysisIssue issue = (CodeAnalysisIssue) invoke(
                "createIssueFromData",
                new Class<?>[]{Map.class, String.class, String.class, String.class},
                new HashMap<>(basicIssue()), "legacy", "author-id", "author");
        assertThat(issue).isNotNull();

        CodeAnalysisIssue hashFailure = new CodeAnalysisIssue();
        hashFailure.setFilePath("A.java");
        hashFailure.setLineNumber(2);
        hashFailure.setIssueCategory(IssueCategory.CODE_QUALITY);
        hashFailure.setTitle("Title");
        Map<String, String> brokenContents = new HashMap<>() {
            @Override
            public boolean containsKey(Object key) {
                throw new IllegalStateException("broken contents");
            }
        };
        invoke("computeTrackingHashes",
                new Class<?>[]{CodeAnalysisIssue.class, Map.class, String.class},
                hashFailure, brokenContents, null);
        assertThat(hashFailure.getIssueFingerprint()).isNull();
    }

    @Test
    void branchIssueQueriesCoverEveryDeduplicationPath() {
        CodeAnalysisIssue superseded = issue(1L, 1L, "superseded", "old-content", null);
        CodeAnalysisIssue successor = issue(2L, 2L, null, null, 1L);
        CodeAnalysisIssue exactOld = issue(3L, 1L, "same", "content-a", null);
        CodeAnalysisIssue exactNew = issue(4L, 2L, "same", "content-a", null);
        CodeAnalysisIssue exactOlder = issue(5L, 0L, "same", "content-a", null);
        CodeAnalysisIssue noFingerprint = issue(6L, 1L, null, null, null);
        CodeAnalysisIssue contentOld = issue(7L, 1L, "unique-1", "shared", null);
        CodeAnalysisIssue contentNew = issue(8L, 2L, "unique-2", "shared", null);
        List<CodeAnalysisIssue> all = List.of(
                superseded, successor, exactOld, exactNew, exactOlder,
                noFingerprint, contentOld, contentNew);

        when(issueRepository.findByProjectIdAndBranchNameAndFilePath(7L, "main", "A.java"))
                .thenReturn(all);
        when(issueRepository.findByProjectIdAndBranchName(7L, "main"))
                .thenReturn(all);
        when(issueRepository.findByProjectIdAndPrNumber(7L, 42L))
                .thenReturn(all);
        when(issueRepository.findByProjectIdAndPrNumberAndFilePath(7L, 42L, "A.java"))
                .thenReturn(all);
        when(issueRepository.findByProjectIdAndPrNumberLatestVersion(7L, 42L))
                .thenReturn(List.of(contentNew));
        when(issueRepository.findByProjectIdAndPrNumberAndFilePathLatestVersion(
                7L, 42L, "A.java"))
                .thenReturn(List.of(exactNew));

        assertThat(service.findIssuesByBranchAndFilePath(7L, "main", "A.java"))
                .contains(successor, exactNew, noFingerprint, contentNew)
                .doesNotContain(superseded, exactOld, exactOlder, contentOld);
        assertThat(service.findIssuesByBranch(7L, "main")).hasSize(4);
        assertThat(service.findIssuesByPrNumber(7L, 42L)).hasSize(4);
        assertThat(service.findIssuesByPrNumberAndFilePath(7L, 42L, "A.java")).hasSize(4);
        assertThat(service.findIssuesByPrNumberLatestVersion(7L, 42L))
                .containsExactly(contentNew);
        assertThat(service.findIssuesByPrNumberAndFilePathLatestVersion(7L, 42L, "A.java"))
                .containsExactly(exactNew);

        when(issueRepository.findByProjectIdAndBranchName(7L, "empty")).thenReturn(null);
        assertThat(service.findIssuesByBranch(7L, "empty")).isEmpty();
    }

    @Test
    void remainingDelegatesContextHashesAndStatsGettersAreCovered() {
        when(issueRepository.countDistinctFilePathsByAnalysisId(9L)).thenReturn(3);
        assertThat(service.countDistinctFilePathsByAnalysisId(9L)).isEqualTo(3);
        when(repository.findLatestByProjectIdAndBranchName(7L, "main"))
                .thenReturn(Optional.empty());
        assertThat(service.findLatestByProjectIdAndBranch(7L, "main")).isEmpty();

        LineHashSequence hashes = LineHashSequence.from("one\ntwo\nthree\nfour\n");
        assertThat(CodeAnalysisService.computeContextHash(hashes, 2, 4, null))
                .isEqualTo(hashes.getRangeHash(2, 4));
        assertThat(CodeAnalysisService.computeContextHash(hashes, 2, null, null))
                .isEqualTo(hashes.getContextHash(2, 1));

        List<Object[]> files = List.<Object[]>of(new Object[]{"A.java", 3L});
        CodeAnalysisService.AnalysisStats stats = new CodeAnalysisService.AnalysisStats(
                2, 1.5, 1, 2, 3, 4, files);
        assertThat(stats.getMostProblematicFiles()).isSameAs(files);
        assertThat(stats.getTotalIssues()).isEqualTo(10);
    }

    @Test
    void reviewedCommitLookupSkipsMissingAndInvalidPrNumbers() {
        CodeAnalysis missing = new CodeAnalysis();
        CodeAnalysis invalid = new CodeAnalysis();
        invalid.setPrNumber(0L);
        CodeAnalysis valid = new CodeAnalysis();
        valid.setPrNumber(42L);
        when(repository.findPrAnalysesByProjectIdAndCommitHash(7L, "head", "main"))
                .thenReturn(List.of(missing, invalid, valid));

        assertThat(service.findReviewedPrNumberByCommitHash(7L, "main", "head"))
                .contains(42L);
    }

    @Test
    void qualityGateSuccessAndInactiveProjectFallbackAreCovered() throws Throwable {
        QualityGate active = new QualityGate();
        active.setActive(true);
        active.setName("strict");
        project.setQualityGate(active);
        when(repository.findByProjectIdAndCommitHashAndPrNumber(7L, "quality", 42L))
                .thenReturn(Optional.empty());
        when(repository.findMaxPrVersion(7L, 42L)).thenReturn(Optional.empty());
        when(repository.save(any(CodeAnalysis.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(qualityGateEvaluator.evaluate(any(), any()))
                .thenReturn(new QualityGateResult(
                        AnalysisResult.PASSED, "strict", List.of()));

        CodeAnalysis evaluated = service.createAnalysisFromAiResponse(
                project,
                Map.of("comment", "review", "issues", List.of()),
                42L,
                "main",
                "feature",
                "quality",
                null,
                null);
        assertThat(evaluated.getAnalysisResult()).isEqualTo(AnalysisResult.PASSED);

        Project inactiveProject = new Project();
        ReflectionTestUtils.setField(inactiveProject, "id", 8L);
        QualityGate inactive = new QualityGate();
        inactive.setActive(false);
        inactiveProject.setQualityGate(inactive);
        Workspace workspace = new Workspace();
        ReflectionTestUtils.setField(workspace, "id", 9L);
        inactiveProject.setWorkspace(workspace);
        when(qualityGateRepository.findDefaultWithConditions(9L))
                .thenReturn(Optional.empty());
        CodeAnalysis inactiveAnalysis = new CodeAnalysis();
        inactiveAnalysis.setProject(inactiveProject);

        assertThat(invoke("getQualityGateForAnalysis",
                new Class<?>[]{CodeAnalysis.class}, inactiveAnalysis)).isNull();
    }

    @Test
    void nullUtilityInputsAndNullCloneFingerprintAreCovered() throws Throwable {
        assertThat(service.findReviewedPrNumberByCommitHash(7L, "main", null)).isEmpty();
        assertThat(invoke("sanitizeForDb", new Class<?>[]{String.class}, (Object) null))
                .isNull();

        CodeAnalysis source = new CodeAnalysis();
        ReflectionTestUtils.setField(source, "id", 11L);
        source.setProject(project);
        source.setAnalysisType(AnalysisType.PR_REVIEW);
        when(repository.findByProjectIdAndCommitHashAndPrNumber(7L, "clone", 44L))
                .thenReturn(Optional.empty());
        when(repository.findMaxPrVersion(7L, 44L)).thenReturn(Optional.empty());
        when(repository.save(any(CodeAnalysis.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        CodeAnalysis clone = service.cloneAnalysisForPr(
                source, project, 44L, "clone", "main", "feature", null);
        assertThat(clone.getDiffFingerprint()).isNull();
    }

    @Test
    void issueParserCoversBlankUnknownAndNullableAnchorInputs() throws Throwable {
        Map<String, Object> blankUnknown = basicIssue();
        blankUnknown.put("id", true);
        blankUnknown.put("file", " ");
        blankUnknown.put("line", true);
        blankUnknown.put("reason", " ");
        blankUnknown.put("title", " ");
        blankUnknown.put("category", " ");
        blankUnknown.put("scope", " ");
        blankUnknown.put("codeSnippet", " ");
        CodeAnalysisIssue inferredFile = invokeIssue(blankUnknown, "blank", Map.of());
        assertThat(inferredFile.getFilePath()).isEqualTo("unknown");
        assertThat(inferredFile.getLineNumber()).isOne();
        assertThat(inferredFile.getIssueScope()).isEqualTo(IssueScope.FILE);

        Map<String, Object> explicitLine = basicIssue();
        explicitLine.put("line", 1);
        explicitLine.put("scope", "LINE");
        explicitLine.put("codeSnippet", " ");
        assertThat(invokeIssue(explicitLine, "explicit-line", Map.of()).getIssueScope())
                .isEqualTo(IssueScope.FILE);

        Map<String, Object> nullableLine = basicIssue();
        nullableLine.put("line", true);
        nullableLine.put("scope", "LINE");
        nullableLine.put("codeSnippet", "danger();");
        CodeAnalysisIssue anchored = invokeIssue(
                nullableLine, "nullable-line", Map.of("A.java", "danger();\n"));
        assertThat(anchored.getLineNumber()).isOne();

        Map<String, Object> emptyContent = basicIssue();
        emptyContent.put("scope", "LINE");
        emptyContent.put("codeSnippet", "danger();");
        assertThat(invokeIssue(emptyContent, "empty-content", Map.of("A.java", "")))
                .isNotNull();

        assertThat(invokeIssue(basicIssue(), "null-files", null)).isNotNull();

        Map<String, Object> longReason = basicIssue();
        longReason.put("reason", "R".repeat(121) + ". details");
        longReason.put("title", " ");
        CodeAnalysisIssue titled = invokeIssue(longReason, "long-reason", Map.of());
        assertThat(titled.getTitle()).hasSize(120).endsWith("...");
    }

    @Test
    void issueRestorationCoversEveryRejectedOriginalValue() throws Throwable {
        CodeAnalysisIssue nullOriginal = new CodeAnalysisIssue();
        when(issueRepository.findById(1L)).thenReturn(Optional.of(nullOriginal));
        CodeAnalysisIssue placeholderOriginal = new CodeAnalysisIssue();
        placeholderOriginal.setSuggestedFixDiff("No suggested fix provided");
        placeholderOriginal.setSuggestedFixDescription(" ");
        when(issueRepository.findById(2L)).thenReturn(Optional.of(placeholderOriginal));
        CodeAnalysisIssue shortOriginal = new CodeAnalysisIssue();
        shortOriginal.setSuggestedFixDiff("too short");
        shortOriginal.setSuggestedFixDescription(
                "No suggested fix description provided");
        when(issueRepository.findById(3L)).thenReturn(Optional.of(shortOriginal));

        Map<String, Object> nullValues = restorationIssue(1L);
        CodeAnalysisIssue restoredNull = invokeIssue(nullValues, "null-original", Map.of());
        assertThat(restoredNull.isResolved()).isTrue();
        assertThat(restoredNull.getResolvedDescription())
                .isEqualTo("Resolved in PR review iteration");

        Map<String, Object> blankValues = restorationIssue(2L);
        blankValues.put("resolutionReason", " ");
        CodeAnalysisIssue restoredBlank = invokeIssue(blankValues, "blank-original", Map.of());
        assertThat(restoredBlank.getResolvedDescription())
                .isEqualTo("Resolved in PR review iteration");

        Map<String, Object> explicitValues = restorationIssue(3L);
        explicitValues.put("resolutionReason", "fixed by refactor");
        CodeAnalysisIssue restoredExplicit = invokeIssue(
                explicitValues, "explicit-resolution", Map.of());
        assertThat(restoredExplicit.getResolvedDescription())
                .isEqualTo("fixed by refactor");
    }

    @Test
    void deduplicationAndTrackingPredicateMatricesCoverEmptyAndFalsePaths()
            throws Throwable {
        @SuppressWarnings("unchecked")
        List<CodeAnalysisIssue> nullIssues = (List<CodeAnalysisIssue>) invoke(
                "deduplicateBranchIssues", new Class<?>[]{List.class}, (Object) null);
        @SuppressWarnings("unchecked")
        List<CodeAnalysisIssue> emptyIssues = (List<CodeAnalysisIssue>) invoke(
                "deduplicateBranchIssues", new Class<?>[]{List.class}, List.of());
        assertThat(nullIssues).isEmpty();
        assertThat(emptyIssues).isEmpty();

        CodeAnalysisIssue emptyFingerprints = issue(20L, 1L, "", "", null);
        CodeAnalysisIssue newestContent = issue(21L, 2L, "new", "shared", null);
        CodeAnalysisIssue olderContent = issue(22L, 1L, "old", "shared", null);
        @SuppressWarnings("unchecked")
        List<CodeAnalysisIssue> deduplicated = (List<CodeAnalysisIssue>) invoke(
                "deduplicateBranchIssues",
                new Class<?>[]{List.class},
                List.of(emptyFingerprints, newestContent, olderContent));
        assertThat(deduplicated)
                .contains(emptyFingerprints, newestContent)
                .doesNotContain(olderContent);

        invokeTracking(trackingIssue(null, null), Map.of(), " ");
        invokeTracking(trackingIssue("A.java", 2), null, null);
        invokeTracking(trackingIssue("A.java", 1), Map.of("A.java", "one\ntwo\n"), null);
        invokeTracking(trackingIssue("A.java", 0), Map.of("A.java", "one\n"), "one");
        invokeTracking(trackingIssue("A.java", null), Map.of("A.java", "one\n"), "one");
        invokeTracking(trackingIssue("A.java", 2), Map.of(), null);
        CodeAnalysisIssue reliable = trackingIssue("A.java", 2);
        invokeTracking(reliable, Map.of("A.java", "one\ntwo\n"), " ");
        assertThat(reliable.getLineHash()).isNotBlank();

        LineHashSequence hashes = LineHashSequence.from("one\ntwo\nthree\n");
        assertThat(CodeAnalysisService.computeContextHash(hashes, 2, 2, " "))
                .isEqualTo(hashes.getContextHash(2, 1));
    }

    private Map<String, Object> basicIssue() {
        Map<String, Object> issue = new HashMap<>();
        issue.put("severity", "HIGH");
        issue.put("file", "A.java");
        issue.put("line", 2);
        issue.put("reason", "Issue reason");
        issue.put("category", "CODE_QUALITY");
        return issue;
    }

    private Map<String, Object> issueWithEdgeValues() {
        Map<String, Object> issue = new HashMap<>();
        issue.put("id", "not-a-number");
        issue.put("severity", "LOW");
        issue.put("file", "A.java");
        issue.put("line", "not-a-line");
        issue.put("reason", "First sentence. Details\0removed");
        issue.put("title", " ");
        issue.put("scope", "LINE");
        issue.put("suggestedFixDescription", null);
        issue.put("suggestedFixDiff", null);
        return issue;
    }

    private Map<String, Object> restorationIssue(Long id) {
        Map<String, Object> issue = basicIssue();
        issue.put("id", id);
        issue.put("suggestedFixDiff", "tiny");
        issue.put("suggestedFixDescription", " ");
        issue.put("isResolved", true);
        return issue;
    }

    private CodeAnalysisIssue invokeIssue(
            Map<String, Object> issueData,
            String key,
            Map<String, String> fileContents) throws Throwable {
        return (CodeAnalysisIssue) invoke(
                "createIssueFromData",
                new Class<?>[]{Map.class, String.class, String.class, String.class,
                        String.class, Long.class, Long.class, Map.class},
                issueData, key, "author-id", "author", "head", 42L, 91L,
                fileContents);
    }

    private CodeAnalysisIssue trackingIssue(String filePath, Integer lineNumber) {
        CodeAnalysisIssue issue = new CodeAnalysisIssue();
        issue.setFilePath(filePath);
        issue.setLineNumber(lineNumber);
        issue.setIssueScope(IssueScope.LINE);
        issue.setIssueCategory(IssueCategory.CODE_QUALITY);
        issue.setTitle("Tracking issue");
        return issue;
    }

    private void invokeTracking(
            CodeAnalysisIssue issue,
            Map<String, String> fileContents,
            String snippet) throws Throwable {
        invoke("computeTrackingHashes",
                new Class<?>[]{CodeAnalysisIssue.class, Map.class, String.class},
                issue, fileContents, snippet);
    }

    private CodeAnalysisIssue issue(
            Long issueId,
            Long analysisId,
            String fingerprint,
            String contentFingerprint,
            Long trackedFrom) {
        CodeAnalysis analysis = new CodeAnalysis();
        ReflectionTestUtils.setField(analysis, "id", analysisId);
        CodeAnalysisIssue issue = new CodeAnalysisIssue();
        ReflectionTestUtils.setField(issue, "id", issueId);
        issue.setAnalysis(analysis);
        issue.setIssueFingerprint(fingerprint);
        issue.setContentFingerprint(contentFingerprint);
        issue.setTrackedFromIssueId(trackedFrom);
        return issue;
    }

    private Object invoke(String name, Class<?>[] parameterTypes, Object... arguments)
            throws Throwable {
        Method method = CodeAnalysisService.class.getDeclaredMethod(name, parameterTypes);
        method.setAccessible(true);
        try {
            return method.invoke(service, arguments);
        } catch (InvocationTargetException error) {
            throw error;
        }
    }
}

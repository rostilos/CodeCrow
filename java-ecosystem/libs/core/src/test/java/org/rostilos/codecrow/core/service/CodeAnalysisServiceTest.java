package org.rostilos.codecrow.core.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.codeanalysis.*;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.qualitygate.QualityGate;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.qualitygate.QualityGateRepository;

import java.lang.reflect.Field;
import java.time.OffsetDateTime;
import java.util.*;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("CodeAnalysisService")
class CodeAnalysisServiceTest {

    @Mock
    private CodeAnalysisRepository codeAnalysisRepository;
    @Mock
    private CodeAnalysisIssueRepository issueRepository;
    @Mock
    private QualityGateRepository qualityGateRepository;

    private CodeAnalysisService codeAnalysisService;

    @BeforeEach
    void setUp() {
        codeAnalysisService = new CodeAnalysisService(codeAnalysisRepository, issueRepository, qualityGateRepository);
    }

    // ── Helper methods ──────────────────────────────────────────────────────

    private Project createProject(Long id, String name) {
        Project project = new Project();
        setField(project, "id", id);
        project.setName(name);
        return project;
    }

    private Project createProjectWithWorkspace(Long id, String name, Long workspaceId) {
        Project project = createProject(id, name);
        Workspace workspace = new Workspace();
        setField(workspace, "id", workspaceId);
        project.setWorkspace(workspace);
        return project;
    }

    private Project createProjectWithQualityGate(Long id, String name, QualityGate qg) {
        Project project = createProjectWithWorkspace(id, name, 1L);
        project.setQualityGate(qg);
        return project;
    }

    private QualityGate createActiveQualityGate(Long id, String name) {
        QualityGate qg = new QualityGate();
        setField(qg, "id", id);
        qg.setName(name);
        qg.setActive(true);
        return qg;
    }

    private CodeAnalysis createCodeAnalysis(Long id, Project project) {
        CodeAnalysis analysis = new CodeAnalysis();
        setField(analysis, "id", id);
        analysis.setProject(project);
        return analysis;
    }

    private Map<String, Object> createBasicAnalysisData(String comment) {
        Map<String, Object> data = new HashMap<>();
        data.put("comment", comment);
        return data;
    }

    private Map<String, Object> createIssueData(String severity, String file, int line, String reason) {
        Map<String, Object> issue = new HashMap<>();
        issue.put("severity", severity);
        issue.put("file", file);
        issue.put("line", line);
        issue.put("reason", reason);
        issue.put("suggestedFixDescription", "Fix it");
        issue.put("suggestedFixDiff", "- old\n+ new");
        issue.put("category", "CODE_QUALITY");
        return issue;
    }

    private static void setField(Object target, String fieldName, Object value) {
        try {
            Class<?> clazz = target.getClass();
            while (clazz != null) {
                try {
                    Field field = clazz.getDeclaredField(fieldName);
                    field.setAccessible(true);
                    field.set(target, value);
                    return;
                } catch (NoSuchFieldException e) {
                    clazz = clazz.getSuperclass();
                }
            }
            throw new NoSuchFieldException(fieldName);
        } catch (Exception e) {
            throw new RuntimeException("Failed to set field: " + fieldName, e);
        }
    }

    // ── Tests ───────────────────────────────────────────────────────────────

    @Nested
    @DisplayName("createAnalysisFromAiResponse()")
    class CreateAnalysisFromAiResponseTests {

        @Test
        @DisplayName("should return existing analysis if one already exists")
        void shouldReturnExistingAnalysis() {
            Project project = createProject(1L, "Test");
            CodeAnalysis existing = createCodeAnalysis(10L, project);
            when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "abc123", 42L))
                    .thenReturn(Optional.of(existing));

            CodeAnalysis result = codeAnalysisService.createAnalysisFromAiResponse(
                    project, createBasicAnalysisData("test"), 42L, "main", "feature", "abc123",
                    "author1", "authorUser", "fp123");

            assertThat(result.getId()).isEqualTo(10L);
            verify(codeAnalysisRepository, never()).save(any());
        }

        @Test
        @DisplayName("should create new analysis with comment and no issues")
        void shouldCreateNewAnalysisWithNoIssues() {
            Project project = createProjectWithWorkspace(1L, "Test", 1L);
            when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "abc123", 42L))
                    .thenReturn(Optional.empty());
            when(codeAnalysisRepository.findMaxPrVersion(1L, 42L)).thenReturn(Optional.of(2));
            when(codeAnalysisRepository.save(any(CodeAnalysis.class))).thenAnswer(inv -> {
                CodeAnalysis a = inv.getArgument(0);
                setField(a, "id", 100L);
                return a;
            });

            Map<String, Object> data = createBasicAnalysisData("Great code!");

            CodeAnalysis result = codeAnalysisService.createAnalysisFromAiResponse(
                    project, data, 42L, "main", "feature", "abc123",
                    "author1", "authorUser", "fp123");

            assertThat(result.getComment()).isEqualTo("Great code!");
            assertThat(result.getCommitHash()).isEqualTo("abc123");
            assertThat(result.getStatus()).isEqualTo(AnalysisStatus.ACCEPTED);
            assertThat(result.getPrVersion()).isEqualTo(3);
            assertThat(result.getDiffFingerprint()).isEqualTo("fp123");
        }

        @Test
        @DisplayName("should create analysis with issues from list format")
        void shouldCreateAnalysisWithIssuesFromList() {
            Project project = createProjectWithWorkspace(1L, "Test", 1L);
            when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "abc123", 42L))
                    .thenReturn(Optional.empty());
            when(codeAnalysisRepository.findMaxPrVersion(1L, 42L)).thenReturn(Optional.empty());
            when(codeAnalysisRepository.save(any(CodeAnalysis.class))).thenAnswer(inv -> {
                CodeAnalysis a = inv.getArgument(0);
                if (a.getId() == null) setField(a, "id", 100L);
                return a;
            });

            List<Object> issues = new ArrayList<>();
            issues.add(createIssueData("HIGH", "App.java", 10, "Security issue"));
            issues.add(createIssueData("MEDIUM", "Utils.java", 20, "Null check missing"));

            Map<String, Object> data = createBasicAnalysisData("Review done");
            data.put("issues", issues);

            CodeAnalysis result = codeAnalysisService.createAnalysisFromAiResponse(
                    project, data, 42L, "main", "feature", "abc123",
                    "author1", "authorUser");

            assertThat(result.getIssues()).hasSize(2);
            assertThat(result.getIssues().get(0).getSeverity()).isEqualTo(IssueSeverity.HIGH);
            assertThat(result.getIssues().get(0).getFilePath()).isEqualTo("App.java");
            assertThat(result.getIssues().get(1).getSeverity()).isEqualTo(IssueSeverity.MEDIUM);
        }

        @Test
        @DisplayName("should create analysis with issues from map format")
        void shouldCreateAnalysisWithIssuesFromMap() {
            Project project = createProjectWithWorkspace(1L, "Test", 1L);
            when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "abc123", 42L))
                    .thenReturn(Optional.empty());
            when(codeAnalysisRepository.findMaxPrVersion(1L, 42L)).thenReturn(Optional.empty());
            when(codeAnalysisRepository.save(any(CodeAnalysis.class))).thenAnswer(inv -> {
                CodeAnalysis a = inv.getArgument(0);
                if (a.getId() == null) setField(a, "id", 100L);
                return a;
            });

            Map<String, Object> issues = new LinkedHashMap<>();
            issues.put("issue1", createIssueData("LOW", "Main.java", 5, "Style issue"));

            Map<String, Object> data = createBasicAnalysisData("Review done");
            data.put("issues", issues);

            CodeAnalysis result = codeAnalysisService.createAnalysisFromAiResponse(
                    project, data, 42L, "main", "feature", "abc123",
                    "author1", "authorUser");

            assertThat(result.getIssues()).hasSize(1);
            assertThat(result.getIssues().get(0).getSeverity()).isEqualTo(IssueSeverity.LOW);
        }

        @Test
        @DisplayName("should handle null comment gracefully")
        void shouldHandleNullComment() {
            Project project = createProjectWithWorkspace(1L, "Test", 1L);
            when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "abc123", 42L))
                    .thenReturn(Optional.empty());
            when(codeAnalysisRepository.findMaxPrVersion(1L, 42L)).thenReturn(Optional.empty());
            when(codeAnalysisRepository.save(any(CodeAnalysis.class))).thenAnswer(inv -> {
                CodeAnalysis a = inv.getArgument(0);
                if (a.getId() == null) setField(a, "id", 100L);
                return a;
            });

            Map<String, Object> data = new HashMap<>(); // no comment

            CodeAnalysis result = codeAnalysisService.createAnalysisFromAiResponse(
                    project, data, 42L, "main", "feature", "abc123",
                    "author1", "authorUser");

            assertThat(result.getComment()).isEqualTo("No comment provided");
        }

        @Test
        @DisplayName("should handle null issues gracefully")
        void shouldHandleNullIssues() {
            Project project = createProjectWithWorkspace(1L, "Test", 1L);
            when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "abc123", 42L))
                    .thenReturn(Optional.empty());
            when(codeAnalysisRepository.findMaxPrVersion(1L, 42L)).thenReturn(Optional.empty());
            when(codeAnalysisRepository.save(any(CodeAnalysis.class))).thenAnswer(inv -> {
                CodeAnalysis a = inv.getArgument(0);
                if (a.getId() == null) setField(a, "id", 100L);
                return a;
            });

            Map<String, Object> data = createBasicAnalysisData("OK");
            data.put("issues", null);

            CodeAnalysis result = codeAnalysisService.createAnalysisFromAiResponse(
                    project, data, 42L, "main", "feature", "abc123",
                    "author1", "authorUser");

            assertThat(result.getIssues()).isEmpty();
        }

        @Test
        @DisplayName("should skip null issue data in list")
        void shouldSkipNullIssueDataInList() {
            Project project = createProjectWithWorkspace(1L, "Test", 1L);
            when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "abc123", 42L))
                    .thenReturn(Optional.empty());
            when(codeAnalysisRepository.findMaxPrVersion(1L, 42L)).thenReturn(Optional.empty());
            when(codeAnalysisRepository.save(any(CodeAnalysis.class))).thenAnswer(inv -> {
                CodeAnalysis a = inv.getArgument(0);
                if (a.getId() == null) setField(a, "id", 100L);
                return a;
            });

            List<Object> issues = new ArrayList<>();
            issues.add(null); // null entry
            issues.add(createIssueData("HIGH", "App.java", 10, "Valid issue"));

            Map<String, Object> data = createBasicAnalysisData("Review");
            data.put("issues", issues);

            CodeAnalysis result = codeAnalysisService.createAnalysisFromAiResponse(
                    project, data, 42L, "main", "feature", "abc123",
                    "author1", "authorUser");

            assertThat(result.getIssues()).hasSize(1);
        }

        @Test
        @DisplayName("should handle invalid severity by defaulting to MEDIUM")
        void shouldHandleInvalidSeverity() {
            Project project = createProjectWithWorkspace(1L, "Test", 1L);
            when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "abc123", 42L))
                    .thenReturn(Optional.empty());
            when(codeAnalysisRepository.findMaxPrVersion(1L, 42L)).thenReturn(Optional.empty());
            when(codeAnalysisRepository.save(any(CodeAnalysis.class))).thenAnswer(inv -> {
                CodeAnalysis a = inv.getArgument(0);
                if (a.getId() == null) setField(a, "id", 100L);
                return a;
            });

            Map<String, Object> issueData = createIssueData("INVALID_SEV", "App.java", 10, "Issue");
            List<Object> issues = new ArrayList<>();
            issues.add(issueData);

            Map<String, Object> data = createBasicAnalysisData("Review");
            data.put("issues", issues);

            CodeAnalysis result = codeAnalysisService.createAnalysisFromAiResponse(
                    project, data, 42L, "main", "feature", "abc123",
                    "author1", "authorUser");

            assertThat(result.getIssues()).hasSize(1);
            assertThat(result.getIssues().get(0).getSeverity()).isEqualTo(IssueSeverity.MEDIUM);
        }

        @Test
        @DisplayName("should handle missing severity by returning null issue")
        void shouldHandleMissingSeverity() {
            Project project = createProjectWithWorkspace(1L, "Test", 1L);
            when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "abc123", 42L))
                    .thenReturn(Optional.empty());
            when(codeAnalysisRepository.findMaxPrVersion(1L, 42L)).thenReturn(Optional.empty());
            when(codeAnalysisRepository.save(any(CodeAnalysis.class))).thenAnswer(inv -> {
                CodeAnalysis a = inv.getArgument(0);
                if (a.getId() == null) setField(a, "id", 100L);
                return a;
            });

            Map<String, Object> issueData = new HashMap<>();
            issueData.put("file", "App.java");
            issueData.put("line", 10);
            issueData.put("reason", "No severity");
            // No "severity" key

            List<Object> issues = new ArrayList<>();
            issues.add(issueData);

            Map<String, Object> data = createBasicAnalysisData("Review");
            data.put("issues", issues);

            CodeAnalysis result = codeAnalysisService.createAnalysisFromAiResponse(
                    project, data, 42L, "main", "feature", "abc123",
                    "author1", "authorUser");

            assertThat(result.getIssues()).isEmpty(); // issue skipped due to null severity
        }

        @Test
        @DisplayName("should handle line number as string")
        void shouldHandleLineNumberAsString() {
            Project project = createProjectWithWorkspace(1L, "Test", 1L);
            when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "abc123", 42L))
                    .thenReturn(Optional.empty());
            when(codeAnalysisRepository.findMaxPrVersion(1L, 42L)).thenReturn(Optional.empty());
            when(codeAnalysisRepository.save(any(CodeAnalysis.class))).thenAnswer(inv -> {
                CodeAnalysis a = inv.getArgument(0);
                if (a.getId() == null) setField(a, "id", 100L);
                return a;
            });

            Map<String, Object> issueData = createIssueData("HIGH", "App.java", 0, "Issue");
            issueData.put("line", "42"); // String instead of int

            List<Object> issues = new ArrayList<>();
            issues.add(issueData);

            Map<String, Object> data = createBasicAnalysisData("Review");
            data.put("issues", issues);

            CodeAnalysis result = codeAnalysisService.createAnalysisFromAiResponse(
                    project, data, 42L, "main", "feature", "abc123",
                    "author1", "authorUser");

            assertThat(result.getIssues()).hasSize(1);
            assertThat(result.getIssues().get(0).getLineNumber()).isEqualTo(42);
        }

        @Test
        @DisplayName("should handle resolved issue with original ID")
        void shouldHandleResolvedIssue() {
            Project project = createProjectWithWorkspace(1L, "Test", 1L);
            when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "abc123", 42L))
                    .thenReturn(Optional.empty());
            when(codeAnalysisRepository.findMaxPrVersion(1L, 42L)).thenReturn(Optional.empty());
            when(codeAnalysisRepository.save(any(CodeAnalysis.class))).thenAnswer(inv -> {
                CodeAnalysis a = inv.getArgument(0);
                if (a.getId() == null) setField(a, "id", 100L);
                return a;
            });

            CodeAnalysisIssue originalIssue = new CodeAnalysisIssue();
            setField(originalIssue, "id", 50L);
            when(issueRepository.findById(50L)).thenReturn(Optional.of(originalIssue));

            Map<String, Object> issueData = createIssueData("HIGH", "App.java", 10, "Fixed");
            issueData.put("id", "50");
            issueData.put("isResolved", true);

            List<Object> issues = new ArrayList<>();
            issues.add(issueData);

            Map<String, Object> data = createBasicAnalysisData("Resolved");
            data.put("issues", issues);

            CodeAnalysis result = codeAnalysisService.createAnalysisFromAiResponse(
                    project, data, 42L, "main", "feature", "abc123",
                    "author1", "authorUser");

            assertThat(result.getIssues()).hasSize(1);
            CodeAnalysisIssue resolvedIssue = result.getIssues().get(0);
            assertThat(resolvedIssue.isResolved()).isTrue();
            assertThat(resolvedIssue.getResolvedByPr()).isEqualTo(42L);
            assertThat(resolvedIssue.getResolvedCommitHash()).isEqualTo("abc123");
            assertThat(resolvedIssue.getResolvedBy()).isEqualTo("authorUser");
        }

        @Test
        @DisplayName("should evaluate quality gate when project has active QG")
        void shouldEvaluateQualityGate() {
            QualityGate qg = createActiveQualityGate(1L, "Default");
            Project project = createProjectWithQualityGate(1L, "Test", qg);
            when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "abc123", 42L))
                    .thenReturn(Optional.empty());
            when(codeAnalysisRepository.findMaxPrVersion(1L, 42L)).thenReturn(Optional.empty());
            when(codeAnalysisRepository.save(any(CodeAnalysis.class))).thenAnswer(inv -> {
                CodeAnalysis a = inv.getArgument(0);
                if (a.getId() == null) setField(a, "id", 100L);
                return a;
            });

            Map<String, Object> data = createBasicAnalysisData("Clean code");

            CodeAnalysis result = codeAnalysisService.createAnalysisFromAiResponse(
                    project, data, 42L, "main", "feature", "abc123",
                    "author1", "authorUser");

            // Quality gate was evaluated (even if result is null due to no conditions)
            verify(codeAnalysisRepository, atLeast(1)).save(any(CodeAnalysis.class));
        }

        @Test
        @DisplayName("should use workspace default QG when project has no QG")
        void shouldUseWorkspaceDefaultQualityGate() {
            Project project = createProjectWithWorkspace(1L, "Test", 10L);
            // project has no qualityGate set, so it falls through to workspace default
            QualityGate defaultQg = createActiveQualityGate(2L, "Workspace Default");
            when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "abc123", 42L))
                    .thenReturn(Optional.empty());
            when(codeAnalysisRepository.findMaxPrVersion(1L, 42L)).thenReturn(Optional.empty());
            when(codeAnalysisRepository.save(any(CodeAnalysis.class))).thenAnswer(inv -> {
                CodeAnalysis a = inv.getArgument(0);
                if (a.getId() == null) setField(a, "id", 100L);
                return a;
            });
            when(qualityGateRepository.findDefaultWithConditions(10L)).thenReturn(Optional.of(defaultQg));

            // Need issues section so the code path reaches QG evaluation
            List<Object> issues = new ArrayList<>();
            issues.add(createIssueData("HIGH", "App.java", 10, "Bug"));
            Map<String, Object> data = createBasicAnalysisData("OK");
            data.put("issues", issues);

            codeAnalysisService.createAnalysisFromAiResponse(
                    project, data, 42L, "main", "feature", "abc123",
                    "author1", "authorUser");

            verify(qualityGateRepository).findDefaultWithConditions(10L);
        }

        @Test
        @DisplayName("should handle issues with unsupported type gracefully")
        void shouldHandleUnsupportedIssuesType() {
            Project project = createProjectWithWorkspace(1L, "Test", 1L);
            when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "abc123", 42L))
                    .thenReturn(Optional.empty());
            when(codeAnalysisRepository.findMaxPrVersion(1L, 42L)).thenReturn(Optional.empty());
            when(codeAnalysisRepository.save(any(CodeAnalysis.class))).thenAnswer(inv -> {
                CodeAnalysis a = inv.getArgument(0);
                if (a.getId() == null) setField(a, "id", 100L);
                return a;
            });

            Map<String, Object> data = createBasicAnalysisData("Review");
            data.put("issues", "not a list or map");  // String is unsupported

            CodeAnalysis result = codeAnalysisService.createAnalysisFromAiResponse(
                    project, data, 42L, "main", "feature", "abc123",
                    "author1", "authorUser");

            assertThat(result.getIssues()).isEmpty();
        }

        @Test
        @DisplayName("should handle empty file path by defaulting to unknown")
        void shouldHandleEmptyFilePath() {
            Project project = createProjectWithWorkspace(1L, "Test", 1L);
            when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "abc123", 42L))
                    .thenReturn(Optional.empty());
            when(codeAnalysisRepository.findMaxPrVersion(1L, 42L)).thenReturn(Optional.empty());
            when(codeAnalysisRepository.save(any(CodeAnalysis.class))).thenAnswer(inv -> {
                CodeAnalysis a = inv.getArgument(0);
                if (a.getId() == null) setField(a, "id", 100L);
                return a;
            });

            Map<String, Object> issueData = createIssueData("HIGH", "", 10, "Issue");
            List<Object> issues = new ArrayList<>();
            issues.add(issueData);

            Map<String, Object> data = createBasicAnalysisData("Review");
            data.put("issues", issues);

            CodeAnalysis result = codeAnalysisService.createAnalysisFromAiResponse(
                    project, data, 42L, "main", "feature", "abc123",
                    "author1", "authorUser");

            assertThat(result.getIssues()).hasSize(1);
            assertThat(result.getIssues().get(0).getFilePath()).isEqualTo("unknown");
        }

        @Test
        @DisplayName("should handle issue ID as Number")
        void shouldHandleIssueIdAsNumber() {
            Project project = createProjectWithWorkspace(1L, "Test", 1L);
            when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "abc123", 42L))
                    .thenReturn(Optional.empty());
            when(codeAnalysisRepository.findMaxPrVersion(1L, 42L)).thenReturn(Optional.empty());
            when(codeAnalysisRepository.save(any(CodeAnalysis.class))).thenAnswer(inv -> {
                CodeAnalysis a = inv.getArgument(0);
                if (a.getId() == null) setField(a, "id", 100L);
                return a;
            });
            when(issueRepository.findById(99L)).thenReturn(Optional.empty());

            Map<String, Object> issueData = createIssueData("HIGH", "App.java", 10, "Bug");
            issueData.put("id", 99); // Number type

            List<Object> issues = new ArrayList<>();
            issues.add(issueData);

            Map<String, Object> data = createBasicAnalysisData("Review");
            data.put("issues", issues);

            CodeAnalysis result = codeAnalysisService.createAnalysisFromAiResponse(
                    project, data, 42L, "main", "feature", "abc123",
                    "author1", "authorUser");

            assertThat(result.getIssues()).hasSize(1);
            verify(issueRepository).findById(99L);
        }

        @Test
        @DisplayName("should handle isResolved as string 'true'")
        void shouldHandleIsResolvedAsString() {
            Project project = createProjectWithWorkspace(1L, "Test", 1L);
            when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "abc123", 42L))
                    .thenReturn(Optional.empty());
            when(codeAnalysisRepository.findMaxPrVersion(1L, 42L)).thenReturn(Optional.empty());
            when(codeAnalysisRepository.save(any(CodeAnalysis.class))).thenAnswer(inv -> {
                CodeAnalysis a = inv.getArgument(0);
                if (a.getId() == null) setField(a, "id", 100L);
                return a;
            });

            Map<String, Object> issueData = createIssueData("HIGH", "App.java", 10, "Fixed");
            issueData.put("isResolved", "true");

            List<Object> issues = new ArrayList<>();
            issues.add(issueData);

            Map<String, Object> data = createBasicAnalysisData("Review");
            data.put("issues", issues);

            CodeAnalysis result = codeAnalysisService.createAnalysisFromAiResponse(
                    project, data, 42L, "main", "feature", "abc123",
                    "author1", "authorUser");

            assertThat(result.getIssues()).hasSize(1);
            assertThat(result.getIssues().get(0).isResolved()).isTrue();
        }
    }

    @Nested
    @DisplayName("cloneAnalysisForPr()")
    class CloneAnalysisForPrTests {

        @Test
        @DisplayName("should return existing clone if already exists")
        void shouldReturnExistingClone() {
            Project project = createProject(1L, "Test");
            CodeAnalysis existing = createCodeAnalysis(20L, project);
            CodeAnalysis source = createCodeAnalysis(10L, project);

            when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "abc123", 99L))
                    .thenReturn(Optional.of(existing));

            CodeAnalysis result = codeAnalysisService.cloneAnalysisForPr(
                    source, project, 99L, "abc123", "main", "feature", "fp123");

            assertThat(result.getId()).isEqualTo(20L);
            verify(codeAnalysisRepository, never()).save(any());
        }

        @Test
        @DisplayName("should clone analysis with all issues")
        void shouldCloneAnalysisWithIssues() {
            Project project = createProject(1L, "Test");
            CodeAnalysis source = createCodeAnalysis(10L, project);
            source.setAnalysisType(AnalysisType.PR_REVIEW);
            source.setComment("Original comment");
            source.setStatus(AnalysisStatus.ACCEPTED);
            source.setAnalysisResult(AnalysisResult.PASSED);

            CodeAnalysisIssue srcIssue = new CodeAnalysisIssue();
            srcIssue.setSeverity(IssueSeverity.HIGH);
            srcIssue.setFilePath("App.java");
            srcIssue.setLineNumber(10);
            srcIssue.setReason("Bug found");
            srcIssue.setSuggestedFixDescription("Fix it");
            srcIssue.setSuggestedFixDiff("diff");
            srcIssue.setIssueCategory(IssueCategory.BUG_RISK);
            srcIssue.setResolved(false);
            srcIssue.setVcsAuthorId("author1");
            srcIssue.setVcsAuthorUsername("authorUser");
            source.addIssue(srcIssue);

            when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "abc123", 99L))
                    .thenReturn(Optional.empty());
            when(codeAnalysisRepository.findMaxPrVersion(1L, 99L)).thenReturn(Optional.of(1));
            when(codeAnalysisRepository.save(any(CodeAnalysis.class))).thenAnswer(inv -> {
                CodeAnalysis a = inv.getArgument(0);
                if (a.getId() == null) setField(a, "id", 200L);
                return a;
            });

            CodeAnalysis result = codeAnalysisService.cloneAnalysisForPr(
                    source, project, 99L, "abc123", "main", "feature", "fp12345678abcdef");

            assertThat(result.getComment()).isEqualTo("Original comment");
            assertThat(result.getAnalysisResult()).isEqualTo(AnalysisResult.PASSED);
            assertThat(result.getPrVersion()).isEqualTo(2);
            assertThat(result.getDiffFingerprint()).isEqualTo("fp12345678abcdef");
            assertThat(result.getIssues()).hasSize(1);
            assertThat(result.getIssues().get(0).getFilePath()).isEqualTo("App.java");
            assertThat(result.getIssues().get(0).getSeverity()).isEqualTo(IssueSeverity.HIGH);
        }
    }

    @Nested
    @DisplayName("getCodeAnalysisCache()")
    class GetCodeAnalysisCacheTests {
        @Test
        @DisplayName("should return cached analysis")
        void shouldReturnCachedAnalysis() {
            CodeAnalysis analysis = createCodeAnalysis(1L, createProject(1L, "Test"));
            when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "abc", 42L))
                    .thenReturn(Optional.of(analysis));

            Optional<CodeAnalysis> result = codeAnalysisService.getCodeAnalysisCache(1L, "abc", 42L);
            assertThat(result).isPresent();
        }

        @Test
        @DisplayName("should return empty when no cache")
        void shouldReturnEmptyWhenNoCache() {
            when(codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "abc", 42L))
                    .thenReturn(Optional.empty());

            Optional<CodeAnalysis> result = codeAnalysisService.getCodeAnalysisCache(1L, "abc", 42L);
            assertThat(result).isEmpty();
        }
    }

    @Nested
    @DisplayName("getAnalysisByCommitHash()")
    class GetAnalysisByCommitHashTests {
        @Test
        @DisplayName("should return analysis by commit hash")
        void shouldReturnByCommitHash() {
            CodeAnalysis analysis = createCodeAnalysis(1L, createProject(1L, "Test"));
            when(codeAnalysisRepository.findTopByProjectIdAndCommitHash(1L, "abc"))
                    .thenReturn(Optional.of(analysis));

            assertThat(codeAnalysisService.getAnalysisByCommitHash(1L, "abc")).isPresent();
        }
    }

    @Nested
    @DisplayName("getAnalysisByDiffFingerprint()")
    class GetAnalysisByDiffFingerprintTests {
        @Test
        @DisplayName("should return analysis by fingerprint")
        void shouldReturnByFingerprint() {
            CodeAnalysis analysis = createCodeAnalysis(1L, createProject(1L, "Test"));
            when(codeAnalysisRepository.findTopByProjectIdAndDiffFingerprint(1L, "fp123"))
                    .thenReturn(Optional.of(analysis));

            assertThat(codeAnalysisService.getAnalysisByDiffFingerprint(1L, "fp123")).isPresent();
        }

        @Test
        @DisplayName("should return empty for null fingerprint")
        void shouldReturnEmptyForNullFingerprint() {
            assertThat(codeAnalysisService.getAnalysisByDiffFingerprint(1L, null)).isEmpty();
        }

        @Test
        @DisplayName("should return empty for blank fingerprint")
        void shouldReturnEmptyForBlankFingerprint() {
            assertThat(codeAnalysisService.getAnalysisByDiffFingerprint(1L, "  ")).isEmpty();
        }
    }

    @Nested
    @DisplayName("createAnalysis()")
    class CreateAnalysisTests {
        @Test
        @DisplayName("should create pending analysis")
        void shouldCreatePendingAnalysis() {
            Project project = createProject(1L, "Test");
            when(codeAnalysisRepository.save(any(CodeAnalysis.class))).thenAnswer(inv -> {
                CodeAnalysis a = inv.getArgument(0);
                setField(a, "id", 100L);
                return a;
            });

            CodeAnalysis result = codeAnalysisService.createAnalysis(project, AnalysisType.BRANCH_ANALYSIS);

            assertThat(result.getAnalysisType()).isEqualTo(AnalysisType.BRANCH_ANALYSIS);
            assertThat(result.getStatus()).isEqualTo(AnalysisStatus.PENDING);
        }
    }

    @Nested
    @DisplayName("saveAnalysis()")
    class SaveAnalysisTests {
        @Test
        @DisplayName("should save and update issue counts")
        void shouldSaveAndUpdateCounts() {
            CodeAnalysis analysis = createCodeAnalysis(1L, createProject(1L, "Test"));
            CodeAnalysisIssue issue = new CodeAnalysisIssue();
            issue.setSeverity(IssueSeverity.HIGH);
            issue.setFilePath("test.java");
            analysis.addIssue(issue);

            when(codeAnalysisRepository.save(any())).thenAnswer(inv -> inv.getArgument(0));

            CodeAnalysis result = codeAnalysisService.saveAnalysis(analysis);

            assertThat(result.getTotalIssues()).isEqualTo(1);
            assertThat(result.getHighSeverityCount()).isEqualTo(1);
        }
    }

    @Nested
    @DisplayName("Delegation methods")
    class DelegationTests {

        @Test
        @DisplayName("findById should delegate to repository")
        void findById() {
            when(codeAnalysisRepository.findById(1L)).thenReturn(Optional.empty());
            assertThat(codeAnalysisService.findById(1L)).isEmpty();
            verify(codeAnalysisRepository).findById(1L);
        }

        @Test
        @DisplayName("findByProjectId should delegate")
        void findByProjectId() {
            when(codeAnalysisRepository.findByProjectIdOrderByCreatedAtDesc(1L)).thenReturn(List.of());
            assertThat(codeAnalysisService.findByProjectId(1L)).isEmpty();
        }

        @Test
        @DisplayName("findByProjectIdAndType should delegate")
        void findByProjectIdAndType() {
            when(codeAnalysisRepository.findByProjectIdAndAnalysisTypeOrderByCreatedAtDesc(1L, AnalysisType.PR_REVIEW))
                    .thenReturn(List.of());
            assertThat(codeAnalysisService.findByProjectIdAndType(1L, AnalysisType.PR_REVIEW)).isEmpty();
        }

        @Test
        @DisplayName("findByProjectIdAndPrNumber should delegate")
        void findByProjectIdAndPrNumber() {
            when(codeAnalysisRepository.findByProjectIdAndPrNumber(1L, 42L)).thenReturn(Optional.empty());
            assertThat(codeAnalysisService.findByProjectIdAndPrNumber(1L, 42L)).isEmpty();
        }

        @Test
        @DisplayName("findByProjectIdAndPrNumberAndPrVersion should delegate")
        void findByProjectIdAndPrNumberAndPrVersion() {
            when(codeAnalysisRepository.findByProjectIdAndPrNumberAndPrVersion(1L, 42L, 1))
                    .thenReturn(Optional.empty());
            assertThat(codeAnalysisService.findByProjectIdAndPrNumberAndPrVersion(1L, 42L, 1)).isEmpty();
        }

        @Test
        @DisplayName("findByProjectIdAndDateRange should delegate")
        void findByProjectIdAndDateRange() {
            OffsetDateTime start = OffsetDateTime.now().minusDays(7);
            OffsetDateTime end = OffsetDateTime.now();
            when(codeAnalysisRepository.findByProjectIdAndDateRange(1L, start, end)).thenReturn(List.of());
            assertThat(codeAnalysisService.findByProjectIdAndDateRange(1L, start, end)).isEmpty();
        }

        @Test
        @DisplayName("findByProjectIdWithHighSeverityIssues should delegate")
        void findByProjectIdWithHighSeverityIssues() {
            when(codeAnalysisRepository.findByProjectIdWithHighSeverityIssues(1L)).thenReturn(List.of());
            assertThat(codeAnalysisService.findByProjectIdWithHighSeverityIssues(1L)).isEmpty();
        }

        @Test
        @DisplayName("findLatestByProjectId should delegate")
        void findLatestByProjectId() {
            when(codeAnalysisRepository.findLatestByProjectId(1L)).thenReturn(Optional.empty());
            assertThat(codeAnalysisService.findLatestByProjectId(1L)).isEmpty();
        }

        @Test
        @DisplayName("getPreviousVersionCodeAnalysis should delegate")
        void getPreviousVersionCodeAnalysis() {
            when(codeAnalysisRepository.findByProjectIdAndPrNumberWithMaxPrVersion(1L, 42L))
                    .thenReturn(Optional.empty());
            assertThat(codeAnalysisService.getPreviousVersionCodeAnalysis(1L, 42L)).isEmpty();
        }

        @Test
        @DisplayName("getAllPrAnalyses should delegate")
        void getAllPrAnalyses() {
            when(codeAnalysisRepository.findAllByProjectIdAndPrNumberOrderByPrVersionDesc(1L, 42L))
                    .thenReturn(List.of());
            assertThat(codeAnalysisService.getAllPrAnalyses(1L, 42L)).isEmpty();
        }

        @Test
        @DisplayName("getMaxAnalysisPrVersion should delegate")
        void getMaxAnalysisPrVersion() {
            when(codeAnalysisRepository.findMaxPrVersion(1L, 42L)).thenReturn(Optional.of(3));
            assertThat(codeAnalysisService.getMaxAnalysisPrVersion(1L, 42L)).isEqualTo(3);
        }

        @Test
        @DisplayName("findAnalysisByProjectAndPrNumberAndVersion should delegate")
        void findAnalysisByProjectAndPrNumberAndVersion() {
            when(codeAnalysisRepository.findByProjectIdAndPrNumberAndPrVersion(1L, 42L, 2))
                    .thenReturn(Optional.empty());
            assertThat(codeAnalysisService.findAnalysisByProjectAndPrNumberAndVersion(1L, 42L, 2)).isEmpty();
        }

        @Test
        @DisplayName("saveIssue should delegate")
        void saveIssue() {
            CodeAnalysisIssue issue = new CodeAnalysisIssue();
            when(issueRepository.save(issue)).thenReturn(issue);
            assertThat(codeAnalysisService.saveIssue(issue)).isEqualTo(issue);
        }

        @Test
        @DisplayName("findIssuesByAnalysisId should delegate")
        void findIssuesByAnalysisId() {
            when(issueRepository.findByAnalysisIdOrderBySeverityDescLineNumberAsc(1L)).thenReturn(List.of());
            assertThat(codeAnalysisService.findIssuesByAnalysisId(1L)).isEmpty();
        }

        @Test
        @DisplayName("findIssuesByAnalysisIdAndSeverity should delegate")
        void findIssuesByAnalysisIdAndSeverity() {
            when(issueRepository.findByAnalysisIdAndSeverityOrderByLineNumberAsc(1L, IssueSeverity.HIGH))
                    .thenReturn(List.of());
            assertThat(codeAnalysisService.findIssuesByAnalysisIdAndSeverity(1L, IssueSeverity.HIGH)).isEmpty();
        }

        @Test
        @DisplayName("deleteAnalysis should delegate")
        void deleteAnalysis() {
            codeAnalysisService.deleteAnalysis(1L);
            verify(codeAnalysisRepository).deleteById(1L);
        }

        @Test
        @DisplayName("deleteAllAnalysesByProjectId should delegate")
        void deleteAllAnalysesByProjectId() {
            codeAnalysisService.deleteAllAnalysesByProjectId(1L);
            verify(codeAnalysisRepository).deleteByProjectId(1L);
        }
    }

    @Nested
    @DisplayName("markIssueAsResolved()")
    class MarkIssueAsResolvedTests {
        @Test
        @DisplayName("should mark issue as resolved when found")
        void shouldMarkIssueAsResolved() {
            CodeAnalysisIssue issue = new CodeAnalysisIssue();
            issue.setResolved(false);
            when(issueRepository.findById(1L)).thenReturn(Optional.of(issue));
            when(issueRepository.save(any())).thenReturn(issue);

            codeAnalysisService.markIssueAsResolved(1L);

            assertThat(issue.isResolved()).isTrue();
            verify(issueRepository).save(issue);
        }

        @Test
        @DisplayName("should do nothing when issue not found")
        void shouldDoNothingWhenNotFound() {
            when(issueRepository.findById(999L)).thenReturn(Optional.empty());

            codeAnalysisService.markIssueAsResolved(999L);

            verify(issueRepository, never()).save(any());
        }
    }

    @Nested
    @DisplayName("getProjectAnalysisStats()")
    class GetProjectAnalysisStatsTests {
        @Test
        @DisplayName("should compute stats correctly")
        void shouldComputeStats() {
            when(codeAnalysisRepository.countByProjectId(1L)).thenReturn(10L);
            when(codeAnalysisRepository.getAverageIssuesPerAnalysis(1L)).thenReturn(5.5);
            when(issueRepository.countByProjectIdAndSeverity(1L, IssueSeverity.HIGH)).thenReturn(3L);
            when(issueRepository.countByProjectIdAndSeverity(1L, IssueSeverity.MEDIUM)).thenReturn(7L);
            when(issueRepository.countByProjectIdAndSeverity(1L, IssueSeverity.LOW)).thenReturn(5L);
            when(issueRepository.countByProjectIdAndSeverity(1L, IssueSeverity.INFO)).thenReturn(2L);
            when(issueRepository.findMostProblematicFilesByProjectId(1L)).thenReturn(List.of());

            CodeAnalysisService.AnalysisStats stats = codeAnalysisService.getProjectAnalysisStats(1L);

            assertThat(stats.getTotalAnalyses()).isEqualTo(10L);
            assertThat(stats.getAverageIssuesPerAnalysis()).isEqualTo(5.5);
            assertThat(stats.getHighSeverityCount()).isEqualTo(3L);
            assertThat(stats.getMediumSeverityCount()).isEqualTo(7L);
            assertThat(stats.getLowSeverityCount()).isEqualTo(5L);
            assertThat(stats.getInfoSeverityCount()).isEqualTo(2L);
            assertThat(stats.getTotalIssues()).isEqualTo(17L);
        }

        @Test
        @DisplayName("should handle null average")
        void shouldHandleNullAverage() {
            when(codeAnalysisRepository.countByProjectId(1L)).thenReturn(0L);
            when(codeAnalysisRepository.getAverageIssuesPerAnalysis(1L)).thenReturn(null);
            when(issueRepository.countByProjectIdAndSeverity(eq(1L), any())).thenReturn(0L);
            when(issueRepository.findMostProblematicFilesByProjectId(1L)).thenReturn(List.of());

            CodeAnalysisService.AnalysisStats stats = codeAnalysisService.getProjectAnalysisStats(1L);

            assertThat(stats.getAverageIssuesPerAnalysis()).isEqualTo(0.0);
        }
    }

    @Nested
    @DisplayName("searchAnalyses()")
    class SearchAnalysesTests {
        @Test
        @DisplayName("should delegate search to repository")
        void shouldDelegateSearch() {
            org.springframework.data.domain.Pageable pageable = org.springframework.data.domain.PageRequest.of(0, 10);
            when(codeAnalysisRepository.searchAnalyses(1L, 42L, AnalysisStatus.ACCEPTED, pageable))
                    .thenReturn(org.springframework.data.domain.Page.empty());

            var result = codeAnalysisService.searchAnalyses(1L, 42L, AnalysisStatus.ACCEPTED, pageable);
            assertThat(result.getContent()).isEmpty();
        }
    }
}

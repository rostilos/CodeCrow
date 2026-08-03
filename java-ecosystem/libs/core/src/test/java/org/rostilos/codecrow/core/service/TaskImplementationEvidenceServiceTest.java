package org.rostilos.codecrow.core.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.TaskImplementationEvidence;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.TaskImplementationEvidenceRepository;
import org.springframework.data.domain.PageRequest;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
@DisplayName("TaskImplementationEvidenceService")
class TaskImplementationEvidenceServiceTest {

    @Mock
    private TaskImplementationEvidenceRepository repository;

    private TaskImplementationEvidenceService service;

    @BeforeEach
    void setUp() {
        service = new TaskImplementationEvidenceService(repository);
    }

    @Test
    @DisplayName("persists bounded structured evidence without comment parsing")
    void persistsStructuredEvidence() {
        CodeAnalysis analysis = analysis(101L, "SHOP-42");
        when(repository.findFingerprintsByAnalysisId(101L)).thenReturn(List.of());

        TaskImplementationEvidenceService.PersistenceResult result =
                service.persistFromAnalysisResponse(analysis, payload(
                        "SHOP-42",
                        Map.of(
                                "evidenceRef", "PRF001",
                                "filePath", "src\\Checkout\\NewRelicTracker.php",
                                "hunkId", "hunk-1",
                                "lineStart", 18,
                                "lineEnd", 21,
                                "excerpt", "recordCustomEvent('CouponApplied', $payload);"
                        )));

        assertThat(result.persisted()).isEqualTo(1);
        assertThat(result.rejected()).isZero();
        ArgumentCaptor<List<TaskImplementationEvidence>> captor =
                ArgumentCaptor.forClass(List.class);
        verify(repository).saveAll(captor.capture());
        TaskImplementationEvidence saved = captor.getValue().get(0);
        assertThat(saved.getAnalysis()).isSameAs(analysis);
        assertThat(saved.getProject()).isSameAs(analysis.getProject());
        assertThat(saved.getTaskId()).isEqualTo("SHOP-42");
        assertThat(saved.getFilePath()).isEqualTo("src/Checkout/NewRelicTracker.php");
        assertThat(saved.getLineStart()).isEqualTo(18);
        assertThat(saved.getLineEnd()).isEqualTo(21);
        assertThat(saved.getContentFingerprint()).matches("[0-9a-f]{64}");
    }

    @Test
    @DisplayName("rejects evidence associated with a different task")
    void rejectsTaskMismatch() {
        CodeAnalysis analysis = analysis(101L, "SHOP-42");

        TaskImplementationEvidenceService.PersistenceResult result =
                service.persistFromAnalysisResponse(
                        analysis,
                        payload("OTHER-9", validItem()));

        assertThat(result.persisted()).isZero();
        assertThat(result.rejected()).isEqualTo(1);
        verifyNoInteractions(repository);
    }

    @Test
    @DisplayName("skips duplicate evidence on webhook retry")
    void skipsDuplicateEvidence() {
        CodeAnalysis analysis = analysis(101L, "SHOP-42");
        when(repository.findFingerprintsByAnalysisId(101L)).thenReturn(List.of());
        service.persistFromAnalysisResponse(
                analysis,
                payload("SHOP-42", validItem()));

        ArgumentCaptor<List<TaskImplementationEvidence>> captor =
                ArgumentCaptor.forClass(List.class);
        verify(repository).saveAll(captor.capture());
        String fingerprint = captor.getValue().get(0).getContentFingerprint();

        when(repository.findFingerprintsByAnalysisId(101L))
                .thenReturn(List.of(fingerprint));
        TaskImplementationEvidenceService.PersistenceResult retry =
                service.persistFromAnalysisResponse(
                        analysis,
                        payload("SHOP-42", validItem()));

        assertThat(retry.persisted()).isZero();
        assertThat(retry.duplicate()).isEqualTo(1);
        verify(repository).saveAll(anyList());
    }

    @Test
    @DisplayName("fails open when optional history evidence lookup is unavailable")
    void lookupFailureFailsOpen() {
        when(repository.findForTaskHistory(
                7L, "SHOP-42", 99L, PageRequest.of(0, 160)))
                .thenThrow(new RuntimeException("database temporarily unavailable"));

        assertThat(service.findForTaskHistory(
                7L, "SHOP-42", 99L, 40)).isEmpty();
    }

    @Test
    @DisplayName("keeps earlier task evidence when newer analysis repeats the same receipt")
    void deduplicatesTaskHistoryAcrossAnalysisIterations() {
        TaskImplementationEvidence newest = storedEvidence(42L, "fingerprint-a");
        TaskImplementationEvidence repeated = storedEvidence(42L, "fingerprint-a");
        TaskImplementationEvidence earlier = storedEvidence(42L, "fingerprint-b");
        when(repository.findForTaskHistory(
                7L, "SHOP-42", 99L, PageRequest.of(0, 160)))
                .thenReturn(List.of(newest, repeated, earlier));

        List<TaskImplementationEvidence> result =
                service.findForTaskHistory(7L, "SHOP-42", 99L, 40);

        assertThat(result).containsExactly(newest, earlier);
    }

    @Test
    @DisplayName("does not persist malformed evidence rows")
    void rejectsMalformedRows() {
        CodeAnalysis analysis = analysis(101L, "SHOP-42");
        when(repository.findFingerprintsByAnalysisId(101L)).thenReturn(List.of());

        TaskImplementationEvidenceService.PersistenceResult result =
                service.persistFromAnalysisResponse(
                        analysis,
                        payload("SHOP-42", Map.of(
                                "evidenceRef", "PRF001",
                                "filePath", "src/File.php",
                                "hunkId", "hunk-1",
                                "lineStart", 21,
                                "lineEnd", 18,
                                "excerpt", "invalid range"
                        )));

        assertThat(result.persisted()).isZero();
        assertThat(result.rejected()).isEqualTo(1);
        verify(repository, never()).saveAll(anyList());
    }

    @Test
    @DisplayName("rejects fractional evidence line numbers instead of truncating them")
    void rejectsFractionalLineNumbers() {
        CodeAnalysis analysis = analysis(101L, "SHOP-42");
        when(repository.findFingerprintsByAnalysisId(101L)).thenReturn(List.of());

        TaskImplementationEvidenceService.PersistenceResult result =
                service.persistFromAnalysisResponse(
                        analysis,
                        payload("SHOP-42", Map.of(
                                "evidenceRef", "PRF001",
                                "filePath", "src/File.php",
                                "hunkId", "hunk-1",
                                "lineStart", 18.5,
                                "lineEnd", 21,
                                "excerpt", "fractional start line"
                        )));

        assertThat(result.persisted()).isZero();
        assertThat(result.rejected()).isEqualTo(1);
        verify(repository, never()).saveAll(anyList());
    }

    private Map<String, Object> payload(
            String taskKey,
            Map<String, Object> item) {
        return Map.of(
                "taskKey", taskKey,
                "source", TaskImplementationEvidenceService.SOURCE_DETERMINISTIC_PR_LEDGER,
                "fullEvidenceComplete", true,
                "items", List.of(item));
    }

    private Map<String, Object> validItem() {
        return Map.of(
                "evidenceRef", "PRF001",
                "filePath", "src/Checkout/NewRelicTracker.php",
                "hunkId", "hunk-1",
                "lineStart", 18,
                "lineEnd", 21,
                "excerpt", "recordCustomEvent('CouponApplied', $payload);");
    }

    private CodeAnalysis analysis(Long id, String taskId) {
        Project project = new Project();
        ReflectionTestUtils.setField(project, "id", 7L);
        CodeAnalysis analysis = new CodeAnalysis();
        ReflectionTestUtils.setField(analysis, "id", id);
        analysis.setProject(project);
        analysis.setTaskId(taskId);
        analysis.setPrNumber(99L);
        analysis.setCommitHash("0123456789012345678901234567890123456789");
        return analysis;
    }

    private TaskImplementationEvidence storedEvidence(
            Long prNumber,
            String fingerprint) {
        TaskImplementationEvidence evidence = new TaskImplementationEvidence();
        evidence.setPrNumber(prNumber);
        evidence.setContentFingerprint(fingerprint);
        return evidence;
    }
}

package org.rostilos.codecrow.analysisengine.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.analysis.AnalysisLock;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.analysis.AnalysisLockRepository;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.lang.reflect.Field;
import java.time.OffsetDateTime;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class AnalysisLockServiceTest {

    @Mock
    private AnalysisLockRepository lockRepository;

    @Mock
    private PlatformTransactionManager transactionManager;

    private AnalysisLockService lockService;
    private Project testProject;

    @BeforeEach
    void setUp() throws Exception {
        lockService = new AnalysisLockService(lockRepository, transactionManager);
        
        testProject = new Project();
        setId(testProject, 1L);
        testProject.setName("test-project");
        
        // Set timeout values via reflection
        setField(lockService, "lockTimeoutMinutes", 30);
        setField(lockService, "ragLockTimeoutMinutes", 360);
    }

    private void setId(Object obj, Long id) throws Exception {
        Field field = obj.getClass().getDeclaredField("id");
        field.setAccessible(true);
        field.set(obj, id);
    }

    private void setField(Object obj, String fieldName, Object value) throws Exception {
        Field field = obj.getClass().getDeclaredField(fieldName);
        field.setAccessible(true);
        field.set(obj, value);
    }

    @Test
    void testAcquireLock_Success_NoPreviousLock() {
        String branchName = "main";
        AnalysisLockType lockType = AnalysisLockType.BRANCH_ANALYSIS;

        when(lockRepository.findByProjectIdAndBranchNameAndAnalysisType(1L, branchName, lockType))
                .thenReturn(Optional.empty());

        Optional<String> result = lockService.acquireLock(testProject, branchName, lockType);

        assertThat(result).isPresent();
        assertThat(result.get()).contains("1");
        assertThat(result.get()).contains("main");
        assertThat(result.get()).contains("BRANCH_ANALYSIS");

        verify(lockRepository).findByProjectIdAndBranchNameAndAnalysisType(1L, branchName, lockType);
        verify(lockRepository).saveAndFlush(any(AnalysisLock.class));
    }

    @Test
    void testAcquireLock_NullBranchName_ReturnsEmpty() {
        AnalysisLockType lockType = AnalysisLockType.BRANCH_ANALYSIS;

        Optional<String> result = lockService.acquireLock(testProject, null, lockType);

        assertThat(result).isEmpty();
        verify(lockRepository, never()).findByProjectIdAndBranchNameAndAnalysisType(anyLong(), any(), any());
    }

    @Test
    void testAcquireLock_BlankBranchName_ReturnsEmpty() {
        AnalysisLockType lockType = AnalysisLockType.BRANCH_ANALYSIS;

        Optional<String> result = lockService.acquireLock(testProject, "  ", lockType);

        assertThat(result).isEmpty();
        verify(lockRepository, never()).findByProjectIdAndBranchNameAndAnalysisType(anyLong(), any(), any());
    }

    @Test
    void testAcquireLock_ExistingValidLock_ReturnsEmpty() {
        String branchName = "develop";
        AnalysisLockType lockType = AnalysisLockType.PR_ANALYSIS;

        // Use mock instead of reflection
        AnalysisLock existingLock = mock(AnalysisLock.class);
        when(existingLock.isExpired()).thenReturn(false);
        when(existingLock.getExpiresAt()).thenReturn(OffsetDateTime.now().plusMinutes(10));

        when(lockRepository.findByProjectIdAndBranchNameAndAnalysisType(1L, branchName, lockType))
                .thenReturn(Optional.of(existingLock));

        Optional<String> result = lockService.acquireLock(testProject, branchName, lockType);

        assertThat(result).isEmpty();
        verify(lockRepository, never()).saveAndFlush(any());
        verify(lockRepository, never()).delete(any());
    }

    @Test
    void testAcquireLock_ExpiredLock_RemovesAndAcquiresNew() {
        String branchName = "feature";
        AnalysisLockType lockType = AnalysisLockType.BRANCH_ANALYSIS;

        // Use mock instead of reflection
        AnalysisLock expiredLock = mock(AnalysisLock.class);
        when(expiredLock.isExpired()).thenReturn(true);

        when(lockRepository.findByProjectIdAndBranchNameAndAnalysisType(1L, branchName, lockType))
                .thenReturn(Optional.of(expiredLock));

        Optional<String> result = lockService.acquireLock(testProject, branchName, lockType);

        assertThat(result).isPresent();
        verify(lockRepository).delete(expiredLock);
        verify(lockRepository).flush();
        verify(lockRepository).saveAndFlush(any(AnalysisLock.class));
    }

    @Test
    void testAcquireLock_WithCommitHashAndPrNumber() {
        String branchName = "main";
        AnalysisLockType lockType = AnalysisLockType.PR_ANALYSIS;
        String commitHash = "abc123def456";
        Long prNumber = 42L;

        when(lockRepository.findByProjectIdAndBranchNameAndAnalysisType(1L, branchName, lockType))
                .thenReturn(Optional.empty());

        Optional<String> result = lockService.acquireLock(testProject, branchName, lockType, commitHash, prNumber);

        assertThat(result).isPresent();

        ArgumentCaptor<AnalysisLock> lockCaptor = ArgumentCaptor.forClass(AnalysisLock.class);
        verify(lockRepository).saveAndFlush(lockCaptor.capture());

        AnalysisLock savedLock = lockCaptor.getValue();
        assertThat(savedLock.getCommitHash()).isEqualTo(commitHash);
        assertThat(savedLock.getPrNumber()).isEqualTo(prNumber);
        assertThat(savedLock.getBranchName()).isEqualTo(branchName);
        assertThat(savedLock.getAnalysisType()).isEqualTo(lockType);
    }

    @Test
    void testAcquireLock_DataIntegrityViolation_ReturnsEmpty() {
        String branchName = "main";
        AnalysisLockType lockType = AnalysisLockType.BRANCH_ANALYSIS;

        when(lockRepository.findByProjectIdAndBranchNameAndAnalysisType(1L, branchName, lockType))
                .thenReturn(Optional.empty());
        when(lockRepository.saveAndFlush(any(AnalysisLock.class)))
                .thenThrow(new DataIntegrityViolationException("Duplicate key"));

        Optional<String> result = lockService.acquireLock(testProject, branchName, lockType);

        assertThat(result).isEmpty();
    }

    @Test
    void testAcquireLock_GeneratesCorrectLockKey() {
        String branchName = "main";
        AnalysisLockType lockType = AnalysisLockType.RAG_INDEXING;

        when(lockRepository.findByProjectIdAndBranchNameAndAnalysisType(1L, branchName, lockType))
                .thenReturn(Optional.empty());

        Optional<String> result = lockService.acquireLock(testProject, branchName, lockType);

        assertThat(result).isPresent();
        String lockKey = result.get();
        assertThat(lockKey).matches(".*1.*main.*RAG_INDEXING.*");
    }

    @Test
    void testReleaseLock_Success() {
        String lockKey = "lock-1-main-BRANCH_ANALYSIS";

        when(lockRepository.deleteByLockKey(lockKey))
                .thenReturn(1);

        lockService.releaseLock(lockKey);

        verify(lockRepository).deleteByLockKey(lockKey);
    }

    @Test
    void testReleaseLock_LockNotFound_DoesNotDelete() {
        String lockKey = "nonexistent-lock";

        when(lockRepository.deleteByLockKey(lockKey))
                .thenReturn(0);

        lockService.releaseLock(lockKey);

        verify(lockRepository).deleteByLockKey(lockKey);
    }

    @Test
    void testReleaseLock_NullKey_DoesNothing() {
        lockService.releaseLock(null);

        verify(lockRepository, never()).deleteByLockKey(any());
    }

    @Test
    void testReleaseLock_EmptyKey_DoesNothing() {
        lockService.releaseLock("");

        verify(lockRepository, never()).deleteByLockKey(any());
    }

    @Test
    void testAcquireLock_DifferentLockTypes() {
        String branchName = "main";

        when(lockRepository.findByProjectIdAndBranchNameAndAnalysisType(anyLong(), any(), any()))
                .thenReturn(Optional.empty());

        Optional<String> branchLock = lockService.acquireLock(
                testProject, branchName, AnalysisLockType.BRANCH_ANALYSIS);
        Optional<String> prLock = lockService.acquireLock(
                testProject, branchName, AnalysisLockType.PR_ANALYSIS);
        Optional<String> ragLock = lockService.acquireLock(
                testProject, branchName, AnalysisLockType.RAG_INDEXING);

        assertThat(branchLock).isPresent();
        assertThat(prLock).isPresent();
        assertThat(ragLock).isPresent();

        assertThat(branchLock.get()).isNotEqualTo(prLock.get());
        assertThat(branchLock.get()).isNotEqualTo(ragLock.get());
        assertThat(prLock.get()).isNotEqualTo(ragLock.get());
    }
}

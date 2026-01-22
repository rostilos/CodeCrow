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
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.Consumer;

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

    @Test
    void testAcquireLockWithWait_NullBranchName_ReturnsEmpty() throws Exception {
        setField(lockService, "lockWaitTimeoutMinutes", 1);
        Consumer<Map<String, Object>> consumer = mock(Consumer.class);

        Optional<String> result = lockService.acquireLockWithWait(
                testProject, null, AnalysisLockType.PR_ANALYSIS, "commit", 1L, consumer);

        assertThat(result).isEmpty();
        verify(consumer).accept(argThat(map -> 
            "error".equals(map.get("type")) && 
            map.get("message").toString().contains("branch information is missing")
        ));
    }

    @Test
    void testAcquireLockWithWait_BlankBranchName_ReturnsEmpty() throws Exception {
        setField(lockService, "lockWaitTimeoutMinutes", 1);
        Consumer<Map<String, Object>> consumer = mock(Consumer.class);

        Optional<String> result = lockService.acquireLockWithWait(
                testProject, "  ", AnalysisLockType.PR_ANALYSIS, "commit", 1L, consumer);

        assertThat(result).isEmpty();
        verify(consumer).accept(any());
    }

    @Test
    void testAcquireLockWithWait_BlankBranchName_NullConsumer_ReturnsEmpty() throws Exception {
        setField(lockService, "lockWaitTimeoutMinutes", 1);

        Optional<String> result = lockService.acquireLockWithWait(
                testProject, "", AnalysisLockType.PR_ANALYSIS, "commit", 1L, null);

        assertThat(result).isEmpty();
    }

    @Test
    void testAcquireLock_UnexpectedException_ReturnsEmpty() {
        String branchName = "main";
        AnalysisLockType lockType = AnalysisLockType.BRANCH_ANALYSIS;

        when(lockRepository.findByProjectIdAndBranchNameAndAnalysisType(anyLong(), any(), any()))
                .thenThrow(new RuntimeException("Database connection failed"));

        Optional<String> result = lockService.acquireLock(testProject, branchName, lockType);

        assertThat(result).isEmpty();
    }

    @Test
    void testExtendLock_Success() {
        String lockKey = "lock-1-main-BRANCH_ANALYSIS";
        int additionalMinutes = 15;

        AnalysisLock lock = mock(AnalysisLock.class);
        when(lock.isExpired()).thenReturn(false);
        when(lock.getExpiresAt()).thenReturn(OffsetDateTime.now().plusMinutes(10));

        when(lockRepository.findByLockKey(lockKey)).thenReturn(Optional.of(lock));

        boolean result = lockService.extendLock(lockKey, additionalMinutes);

        assertThat(result).isTrue();
        verify(lock).setExpiresAt(any(OffsetDateTime.class));
        verify(lockRepository).save(lock);
    }

    @Test
    void testExtendLock_LockNotFound_ReturnsFalse() {
        String lockKey = "nonexistent-lock";

        when(lockRepository.findByLockKey(lockKey)).thenReturn(Optional.empty());

        boolean result = lockService.extendLock(lockKey, 15);

        assertThat(result).isFalse();
        verify(lockRepository, never()).save(any());
    }

    @Test
    void testExtendLock_ExpiredLock_ReturnsFalse() {
        String lockKey = "expired-lock";

        AnalysisLock expiredLock = mock(AnalysisLock.class);
        when(expiredLock.isExpired()).thenReturn(true);

        when(lockRepository.findByLockKey(lockKey)).thenReturn(Optional.of(expiredLock));

        boolean result = lockService.extendLock(lockKey, 15);

        assertThat(result).isFalse();
        verify(lockRepository, never()).save(any());
    }

    @Test
    void testIsLocked_ReturnsTrue_WhenActiveLockExists() {
        Long projectId = 1L;
        String branchName = "main";
        AnalysisLockType lockType = AnalysisLockType.BRANCH_ANALYSIS;

        when(lockRepository.existsActiveLock(eq(projectId), eq(branchName), eq(lockType), any(OffsetDateTime.class)))
                .thenReturn(true);

        boolean result = lockService.isLocked(projectId, branchName, lockType);

        assertThat(result).isTrue();
    }

    @Test
    void testIsLocked_ReturnsFalse_WhenNoActiveLock() {
        Long projectId = 1L;
        String branchName = "main";
        AnalysisLockType lockType = AnalysisLockType.BRANCH_ANALYSIS;

        when(lockRepository.existsActiveLock(eq(projectId), eq(branchName), eq(lockType), any(OffsetDateTime.class)))
                .thenReturn(false);

        boolean result = lockService.isLocked(projectId, branchName, lockType);

        assertThat(result).isFalse();
    }

    @Test
    void testCleanupExpiredLocks_DeletesExpiredLocks() {
        when(lockRepository.deleteExpiredLocks(any(OffsetDateTime.class))).thenReturn(5);

        lockService.cleanupExpiredLocks();

        verify(lockRepository).deleteExpiredLocks(any(OffsetDateTime.class));
    }

    @Test
    void testCleanupExpiredLocks_NoExpiredLocks() {
        when(lockRepository.deleteExpiredLocks(any(OffsetDateTime.class))).thenReturn(0);

        lockService.cleanupExpiredLocks();

        verify(lockRepository).deleteExpiredLocks(any(OffsetDateTime.class));
    }

    @Test
    void testGetInstanceId_ReturnsNonEmptyString() {
        String instanceId = lockService.getInstanceId();

        assertThat(instanceId).isNotNull();
        assertThat(instanceId).isNotEmpty();
    }

    @Test
    void testGetLockWaitTimeoutMinutes_ReturnsConfiguredValue() throws Exception {
        setField(lockService, "lockWaitTimeoutMinutes", 10);

        int timeout = lockService.getLockWaitTimeoutMinutes();

        assertThat(timeout).isEqualTo(10);
    }

    @Test
    void testAcquireLock_WithRagLockType_UsesLongerTimeout() {
        String branchName = "main";
        AnalysisLockType lockType = AnalysisLockType.RAG_INDEXING;

        when(lockRepository.findByProjectIdAndBranchNameAndAnalysisType(1L, branchName, lockType))
                .thenReturn(Optional.empty());

        Optional<String> result = lockService.acquireLock(testProject, branchName, lockType);

        assertThat(result).isPresent();

        ArgumentCaptor<AnalysisLock> lockCaptor = ArgumentCaptor.forClass(AnalysisLock.class);
        verify(lockRepository).saveAndFlush(lockCaptor.capture());

        AnalysisLock savedLock = lockCaptor.getValue();
        assertThat(savedLock.getAnalysisType()).isEqualTo(AnalysisLockType.RAG_INDEXING);
    }

    @Test
    void testAcquireLock_SavesCorrectLockProperties() {
        String branchName = "feature-branch";
        AnalysisLockType lockType = AnalysisLockType.BRANCH_ANALYSIS;
        String commitHash = "abc123";
        Long prNumber = 42L;

        when(lockRepository.findByProjectIdAndBranchNameAndAnalysisType(1L, branchName, lockType))
                .thenReturn(Optional.empty());

        lockService.acquireLock(testProject, branchName, lockType, commitHash, prNumber);

        ArgumentCaptor<AnalysisLock> lockCaptor = ArgumentCaptor.forClass(AnalysisLock.class);
        verify(lockRepository).saveAndFlush(lockCaptor.capture());

        AnalysisLock savedLock = lockCaptor.getValue();
        assertThat(savedLock.getProject()).isEqualTo(testProject);
        assertThat(savedLock.getBranchName()).isEqualTo(branchName);
        assertThat(savedLock.getAnalysisType()).isEqualTo(lockType);
        assertThat(savedLock.getCommitHash()).isEqualTo(commitHash);
        assertThat(savedLock.getPrNumber()).isEqualTo(prNumber);
        assertThat(savedLock.getExpiresAt()).isAfter(OffsetDateTime.now());
        assertThat(savedLock.getLockKey()).contains("1").contains("feature-branch").contains("BRANCH_ANALYSIS");
    }

    @Test
    void testAcquireLockWithWait_ImmediateSuccess_NoConsumerMessage() throws Exception {
        setField(lockService, "lockWaitTimeoutMinutes", 1);
        setField(lockService, "lockWaitRetryIntervalSeconds", 1);

        String branchName = "main";
        AnalysisLockType lockType = AnalysisLockType.PR_ANALYSIS;
        Consumer<Map<String, Object>> consumer = mock(Consumer.class);

        when(lockRepository.findByProjectIdAndBranchNameAndAnalysisType(1L, branchName, lockType))
                .thenReturn(Optional.empty());

        Optional<String> result = lockService.acquireLockWithWait(
                testProject, branchName, lockType, "commit", 1L, consumer);

        assertThat(result).isPresent();
        // Consumer should NOT be called for immediate success (attemptCount == 1)
        verify(consumer, never()).accept(any());
    }

    @Test
    void testAcquireLockWithWait_SuccessAfterRetry_SendsLockAcquiredMessage() throws Exception {
        setField(lockService, "lockWaitTimeoutMinutes", 1);
        setField(lockService, "lockWaitRetryIntervalSeconds", 1);

        String branchName = "main";
        AnalysisLockType lockType = AnalysisLockType.PR_ANALYSIS;
        Consumer<Map<String, Object>> consumer = mock(Consumer.class);

        AnalysisLock existingLock = mock(AnalysisLock.class);
        when(existingLock.isExpired()).thenReturn(false);
        when(existingLock.getExpiresAt()).thenReturn(OffsetDateTime.now().plusMinutes(1));

        // First call: lock exists, second call: lock released
        AtomicInteger callCount = new AtomicInteger(0);
        when(lockRepository.findByProjectIdAndBranchNameAndAnalysisType(1L, branchName, lockType))
                .thenAnswer(inv -> {
                    int count = callCount.incrementAndGet();
                    if (count == 1) {
                        return Optional.of(existingLock);
                    }
                    return Optional.empty();
                });

        Optional<String> result = lockService.acquireLockWithWait(
                testProject, branchName, lockType, "commit", 1L, consumer);

        assertThat(result).isPresent();
        // Consumer should be called for wait message and lock_acquired message
        verify(consumer, atLeast(1)).accept(any());
    }
}

package org.rostilos.codecrow.analysisengine.service;

import org.rostilos.codecrow.core.model.analysis.AnalysisLock;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.analysis.AnalysisLockRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Lazy;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.function.Consumer;

@Service
public class AnalysisLockService {

    private static final Logger log = LoggerFactory.getLogger(AnalysisLockService.class);

    private final AnalysisLockRepository lockRepository;
    private final TransactionTemplate requiresNewTransactionTemplate;
    private final String instanceId;

    @Autowired
    @Lazy
    private AnalysisLockService self;

    @Value("${analysis.lock.timeout.minutes:30}")
    private int lockTimeoutMinutes;

    @Value("${analysis.lock.rag.timeout.minutes:360}")
    private int ragLockTimeoutMinutes;

    @Value("${analysis.lock.wait.timeout.minutes:5}")
    private int lockWaitTimeoutMinutes;

    @Value("${analysis.lock.wait.retry.interval.seconds:5}")
    private int lockWaitRetryIntervalSeconds;

    public AnalysisLockService(AnalysisLockRepository lockRepository, PlatformTransactionManager transactionManager) {
        this.lockRepository = lockRepository;
        this.instanceId = UUID.randomUUID().toString();
        
        // Create a TransactionTemplate with REQUIRES_NEW propagation for fully isolated lock inserts
        this.requiresNewTransactionTemplate = new TransactionTemplate(transactionManager);
        this.requiresNewTransactionTemplate.setPropagationBehavior(TransactionDefinition.PROPAGATION_REQUIRES_NEW);
        
        log.info("AnalysisLockService initialized with instance ID: {}", instanceId);
    }

    /**
     * Acquire a lock for the given project/branch/type.
     * Uses programmatic transaction with REQUIRES_NEW to fully isolate from outer transaction.
     */
    public Optional<String> acquireLock(Project project, String branchName, AnalysisLockType lockType) {
        return acquireLock(project, branchName, lockType, null, null);
    }

    /**
     * Acquire a lock for the given project/branch/type with commit hash and PR number.
     * Uses programmatic transaction with REQUIRES_NEW to ensure that any constraint violation
     * during insert does not mark the calling transaction as rollback-only.
     */
    public Optional<String> acquireLock(Project project, String branchName, AnalysisLockType lockType,
            String commitHash, Long prNumber) {

        // Validate branchName - it's required for lock acquisition
        if (branchName == null || branchName.isBlank()) {
            log.error("Cannot acquire lock: branchName is required but was null or blank. " +
                    "project={}, lockType={}, prNumber={}", project.getId(), lockType, prNumber);
            return Optional.empty();
        }

        OffsetDateTime now = OffsetDateTime.now();
        int timeoutMinutes = getTimeoutForLockType(lockType);
        OffsetDateTime expiresAt = now.plusMinutes(timeoutMinutes);

        String lockKey = generateLockKey(project.getId(), branchName, lockType);

        // Execute lock acquisition in a completely isolated REQUIRES_NEW transaction
        // This ensures that any DataIntegrityViolationException during insert
        // will only rollback this nested transaction and not mark the outer transaction rollback-only
        try {
            return requiresNewTransactionTemplate.execute(status -> {
                Optional<AnalysisLock> existingLock = lockRepository.findByProjectIdAndBranchNameAndAnalysisType(
                        project.getId(), branchName, lockType);

                if (existingLock.isPresent()) {
                    AnalysisLock lock = existingLock.get();
                    if (lock.isExpired()) {
                        log.info("Found expired lock, removing and acquiring new lock: {}", lockKey);
                        lockRepository.delete(lock);
                        lockRepository.flush();
                    } else {
                        log.warn("Lock already held for project={}, branch={}, type={}, expires in {} minutes",
                                project.getId(), branchName, lockType,
                                java.time.Duration.between(now, lock.getExpiresAt()).toMinutes());
                        return Optional.<String>empty();
                    }
                }

                AnalysisLock lock = new AnalysisLock();
                lock.setProject(project);
                lock.setBranchName(branchName);
                lock.setAnalysisType(lockType);
                lock.setLockKey(lockKey);
                lock.setOwnerInstanceId(instanceId);
                lock.setExpiresAt(expiresAt);
                lock.setCommitHash(commitHash);
                lock.setPrNumber(prNumber);

                lockRepository.saveAndFlush(lock);
                log.info("Successfully acquired lock: {} (expires: {})", lockKey, expiresAt);
                return Optional.of(lockKey);
            });
        } catch (DataIntegrityViolationException e) {
            // Could be race condition (another instance acquired lock) or constraint violation
            log.warn("Failed to acquire lock (constraint violation): {} - {}", lockKey, e.getMostSpecificCause().getMessage());
            return Optional.empty();
        } catch (Exception e) {
            // Log unexpected errors but don't propagate to avoid marking outer transaction rollback-only
            log.error("Unexpected error acquiring lock {}: {}", lockKey, e.getMessage(), e);
            return Optional.empty();
        }
    }

    /**
     * Attempts to acquire a lock with retry mechanism and timeout.
     * Sends streaming messages to the consumer while waiting for the lock to be
     * released.
     *
     * @param project         The project
     * @param branchName      The branch name
     * @param lockType        The type of analysis lock
     * @param commitHash      The commit hash (optional)
     * @param prNumber        The PR number (optional)
     * @param messageConsumer Consumer for streaming messages while waiting
     * @return Optional containing the lock key if acquired, empty if timeout
     *         exceeded
     */
    public Optional<String> acquireLockWithWait(Project project, String branchName, AnalysisLockType lockType,
            String commitHash, Long prNumber,
            Consumer<Map<String, Object>> messageConsumer) {
        
        // Fail fast if branchName is null - no point retrying
        if (branchName == null || branchName.isBlank()) {
            log.error("Cannot acquire lock with wait: branchName is required but was null or blank. " +
                    "project={}, lockType={}, prNumber={}", project.getId(), lockType, prNumber);
            if (messageConsumer != null) {
                messageConsumer.accept(Map.of(
                    "type", "error",
                    "message", "Cannot start analysis: branch information is missing. Please ensure the PR has valid source branch information."
                ));
            }
            return Optional.empty();
        }
        
        OffsetDateTime startTime = OffsetDateTime.now();
        OffsetDateTime timeout = startTime.plusMinutes(lockWaitTimeoutMinutes);
        int attemptCount = 0;

        while (OffsetDateTime.now().isBefore(timeout)) {
            attemptCount++;

            // Call acquireLock directly (no longer needs proxy since it uses programmatic transaction)
            Optional<String> lockKey = acquireLock(project, branchName, lockType, commitHash, prNumber);

            if (lockKey.isPresent()) {
                if (attemptCount > 1) {
                    if (messageConsumer != null) {
                        Map<String, Object> lockAcquiredMessage = Map.of(
                                "type", "lock_acquired",
                                "message", String.format("Lock acquired after %d attempts (waited %d seconds)",
                                        attemptCount,
                                        Duration.between(startTime, OffsetDateTime.now()).getSeconds()),
                                "lockType", lockType.name(),
                                "branchName", branchName,
                                "attemptCount", attemptCount);
                        messageConsumer.accept(lockAcquiredMessage);
                    }
                    log.info("Lock acquired after {} attempts (waited {} seconds)",
                            attemptCount,
                            Duration.between(startTime, OffsetDateTime.now()).getSeconds());
                }
                return lockKey;
            }

            long waitedSeconds = Duration.between(startTime, OffsetDateTime.now()).getSeconds();
            long remainingSeconds = Duration.between(OffsetDateTime.now(), timeout).getSeconds();

            log.info(
                    "Lock acquisition attempt {} failed for project={}, branch={}, type={}. Waiting {} seconds before retry...",
                    attemptCount, project.getId(), branchName, lockType, lockWaitRetryIntervalSeconds);

            if (messageConsumer != null) {
                try {
                    // Use HashMap instead of Map.of() to allow null values
                    Map<String, Object> lockWaitMessage = new java.util.HashMap<>();
                    lockWaitMessage.put("type", "lock_wait");
                    lockWaitMessage.put("message",
                            String.format("Waiting for lock release... (attempt %d, waited %ds, timeout in %ds)",
                                    attemptCount, waitedSeconds, remainingSeconds));
                    lockWaitMessage.put("lockType", lockType.name());
                    lockWaitMessage.put("branchName", branchName);
                    lockWaitMessage.put("attemptCount", attemptCount);
                    lockWaitMessage.put("waitedSeconds", waitedSeconds);
                    lockWaitMessage.put("remainingSeconds", remainingSeconds);
                    log.debug("Sending lock wait message to consumer: {}", lockWaitMessage);
                    messageConsumer.accept(lockWaitMessage);
                    log.debug("Lock wait message sent successfully");
                } catch (Exception e) {
                    log.warn("Failed to send lock wait message: {}", e.getMessage(), e);
                }
            } else {
                log.warn("Message consumer is null, cannot send lock wait message");
            }

            if (OffsetDateTime.now().plusSeconds(lockWaitRetryIntervalSeconds).isAfter(timeout)) {
                break;
            }

            try {
                Thread.sleep(lockWaitRetryIntervalSeconds * 1000L);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                log.warn("Lock acquisition wait interrupted");
                return Optional.empty();
            }
        }

        log.warn("Failed to acquire lock after {} attempts and {} seconds (timeout exceeded)",
                attemptCount, Duration.between(startTime, OffsetDateTime.now()).getSeconds());

        if (messageConsumer != null) {
            try {
                messageConsumer.accept(Map.of(
                        "type", "lock_timeout",
                        "message", "Failed to acquire lock: timeout exceeded",
                        "lockType", lockType.name(),
                        "branchName", branchName,
                        "totalAttempts", attemptCount));
            } catch (Exception e) {
                log.warn("Failed to send lock timeout message: {}", e.getMessage());
            }
        }

        return Optional.empty();
    }

    @Transactional
    public void releaseLock(String lockKey) {
        if (lockKey == null || lockKey.isEmpty()) {
            return;
        }

        int deleted = lockRepository.deleteByLockKey(lockKey);
        if (deleted > 0) {
            log.info("Released lock: {}", lockKey);
        } else {
            log.debug("Lock not found (may have already expired): {}", lockKey);
        }
    }

    @Transactional
    public boolean extendLock(String lockKey, int additionalMinutes) {
        Optional<AnalysisLock> lockOpt = lockRepository.findByLockKey(lockKey);
        if (lockOpt.isEmpty()) {
            log.warn("Cannot extend lock - not found: {}", lockKey);
            return false;
        }

        AnalysisLock lock = lockOpt.get();
        if (lock.isExpired()) {
            log.warn("Cannot extend expired lock: {}", lockKey);
            return false;
        }

        lock.setExpiresAt(lock.getExpiresAt().plusMinutes(additionalMinutes));
        lockRepository.save(lock);
        log.debug("Extended lock {} by {} minutes", lockKey, additionalMinutes);
        return true;
    }

    @Transactional(readOnly = true)
    public boolean isLocked(Long projectId, String branchName, AnalysisLockType lockType) {
        return lockRepository.existsActiveLock(projectId, branchName, lockType, OffsetDateTime.now());
    }

    @Scheduled(fixedDelayString = "${analysis.lock.cleanup.interval.ms:300000}")
    @Transactional
    public void cleanupExpiredLocks() {
        int deleted = lockRepository.deleteExpiredLocks(OffsetDateTime.now());
        if (deleted > 0) {
            log.info("Cleaned up {} expired analysis locks", deleted);
        }
    }

    private String generateLockKey(Long projectId, String branchName, AnalysisLockType lockType) {
        return String.format("lock:%d:%s:%s:%s", projectId, branchName, lockType.name(), UUID.randomUUID());
    }

    private int getTimeoutForLockType(AnalysisLockType lockType) {
        if (lockType == AnalysisLockType.RAG_INDEXING) {
            return ragLockTimeoutMinutes;
        }
        return lockTimeoutMinutes;
    }

    public String getInstanceId() {
        return instanceId;
    }

    public int getLockWaitTimeoutMinutes() {
        return lockWaitTimeoutMinutes;
    }
}

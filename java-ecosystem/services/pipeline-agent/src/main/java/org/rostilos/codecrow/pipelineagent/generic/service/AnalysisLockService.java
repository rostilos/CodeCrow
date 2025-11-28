package org.rostilos.codecrow.pipelineagent.generic.service;

import org.rostilos.codecrow.core.model.analysis.AnalysisLock;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.analysis.AnalysisLockRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

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
    private final String instanceId;

    @Value("${analysis.lock.timeout.minutes:30}")
    private int lockTimeoutMinutes;

    @Value("${analysis.lock.wait.timeout.minutes:5}")
    private int lockWaitTimeoutMinutes;

    @Value("${analysis.lock.wait.retry.interval.seconds:5}")
    private int lockWaitRetryIntervalSeconds;

    public AnalysisLockService(AnalysisLockRepository lockRepository) {
        this.lockRepository = lockRepository;
        this.instanceId = UUID.randomUUID().toString();
        log.info("AnalysisLockService initialized with instance ID: {}", instanceId);
    }

    @Transactional
    public Optional<String> acquireLock(Project project, String branchName, AnalysisLockType lockType) {
        return acquireLock(project, branchName, lockType, null, null);
    }

    @Transactional
    public Optional<String> acquireLock(Project project, String branchName, AnalysisLockType lockType,
                                       String commitHash, Long prNumber) {

        OffsetDateTime now = OffsetDateTime.now();
        OffsetDateTime expiresAt = now.plusMinutes(lockTimeoutMinutes);

        String lockKey = generateLockKey(project.getId(), branchName, lockType);

        Optional<AnalysisLock> existingLock = lockRepository.findByProjectIdAndBranchNameAndAnalysisType(
                project.getId(), branchName, lockType
        );

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
                return Optional.empty();
            }
        }

        try {
            AnalysisLock lock = new AnalysisLock();
            lock.setProject(project);
            lock.setBranchName(branchName);
            lock.setAnalysisType(lockType);
            lock.setLockKey(lockKey);
            lock.setOwnerInstanceId(instanceId);
            lock.setExpiresAt(expiresAt);
            lock.setCommitHash(commitHash);
            lock.setPrNumber(prNumber);

            lockRepository.save(lock);
            log.info("Successfully acquired lock: {} (expires: {})", lockKey, expiresAt);
            return Optional.of(lockKey);
        } catch (DataIntegrityViolationException e) {
            log.warn("Failed to acquire lock (race condition): {}", lockKey);
            return Optional.empty();
        }
    }

    /**
     * Attempts to acquire a lock with retry mechanism and timeout.
     * Sends streaming messages to the consumer while waiting for the lock to be released.
     *
     * @param project The project
     * @param branchName The branch name
     * @param lockType The type of analysis lock
     * @param commitHash The commit hash (optional)
     * @param prNumber The PR number (optional)
     * @param messageConsumer Consumer for streaming messages while waiting
     * @return Optional containing the lock key if acquired, empty if timeout exceeded
     */
    public Optional<String> acquireLockWithWait(Project project, String branchName, AnalysisLockType lockType,
                                                String commitHash, Long prNumber,
                                                Consumer<Map<String, Object>> messageConsumer) {
        OffsetDateTime startTime = OffsetDateTime.now();
        OffsetDateTime timeout = startTime.plusMinutes(lockWaitTimeoutMinutes);
        int attemptCount = 0;

        while (OffsetDateTime.now().isBefore(timeout)) {
            attemptCount++;

            Optional<String> lockKey = acquireLock(project, branchName, lockType, commitHash, prNumber);

            if (lockKey.isPresent()) {
                if (attemptCount > 1) {
                    if(messageConsumer != null) {
                        Map<String, Object> lockAcquiredMessage = Map.of(
                                "type", "lock_acquired",
                                "message", String.format("Lock acquired after %d attempts (waited %d seconds)",
                                        attemptCount,
                                        Duration.between(startTime, OffsetDateTime.now()).getSeconds()),
                                "lockType", lockType.name(),
                                "branchName", branchName,
                                "attemptCount", attemptCount
                        );
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

            log.info("Lock acquisition attempt {} failed for project={}, branch={}, type={}. Waiting {} seconds before retry...",
                    attemptCount, project.getId(), branchName, lockType, lockWaitRetryIntervalSeconds);

            if (messageConsumer != null) {
                try {
                    Map<String, Object> lockWaitMessage = Map.of(
                            "type", "lock_wait",
                            "message", String.format("Waiting for lock release... (attempt %d, waited %ds, timeout in %ds)",
                                    attemptCount, waitedSeconds, remainingSeconds),
                            "lockType", lockType.name(),
                            "branchName", branchName,
                            "attemptCount", attemptCount,
                            "waitedSeconds", waitedSeconds,
                            "remainingSeconds", remainingSeconds
                    );
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
                        "totalAttempts", attemptCount
                ));
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

    public String getInstanceId() {
        return instanceId;
    }

    public int getLockWaitTimeoutMinutes() {
        return lockWaitTimeoutMinutes;
    }
}


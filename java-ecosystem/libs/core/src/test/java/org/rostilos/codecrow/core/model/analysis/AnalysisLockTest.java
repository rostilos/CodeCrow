package org.rostilos.codecrow.core.model.analysis;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

class AnalysisLockTest {

    @Test
    void shouldCreateAnalysisLock() {
        AnalysisLock lock = new AnalysisLock();
        assertThat(lock).isNotNull();
    }

    @Test
    void shouldInitializeCreatedAtOnConstruction() {
        AnalysisLock lock = new AnalysisLock();
        assertThat(lock.getCreatedAt()).isNotNull();
        assertThat(lock.getCreatedAt()).isBeforeOrEqualTo(OffsetDateTime.now());
    }

    @Test
    void shouldSetAndGetId() {
        AnalysisLock lock = new AnalysisLock();
        lock.setId(100L);
        assertThat(lock.getId()).isEqualTo(100L);
    }

    @Test
    void shouldSetAndGetProject() {
        AnalysisLock lock = new AnalysisLock();
        Project project = new Project();
        
        lock.setProject(project);
        
        assertThat(lock.getProject()).isEqualTo(project);
    }

    @Test
    void shouldSetAndGetBranchName() {
        AnalysisLock lock = new AnalysisLock();
        lock.setBranchName("main");
        
        assertThat(lock.getBranchName()).isEqualTo("main");
    }

    @Test
    void shouldSetAndGetAnalysisType() {
        AnalysisLock lock = new AnalysisLock();
        lock.setAnalysisType(AnalysisLockType.PR_ANALYSIS);
        
        assertThat(lock.getAnalysisType()).isEqualTo(AnalysisLockType.PR_ANALYSIS);
    }

    @Test
    void shouldSetAndGetLockKey() {
        AnalysisLock lock = new AnalysisLock();
        String lockKey = "project:123:branch:main:type:PR";
        
        lock.setLockKey(lockKey);
        
        assertThat(lock.getLockKey()).isEqualTo(lockKey);
    }

    @Test
    void shouldSetAndGetOwnerInstanceId() {
        AnalysisLock lock = new AnalysisLock();
        String instanceId = "instance-abc-123";
        
        lock.setOwnerInstanceId(instanceId);
        
        assertThat(lock.getOwnerInstanceId()).isEqualTo(instanceId);
    }

    @Test
    void shouldSetAndGetCreatedAt() {
        AnalysisLock lock = new AnalysisLock();
        OffsetDateTime timestamp = OffsetDateTime.now().minusMinutes(5);
        
        lock.setCreatedAt(timestamp);
        
        assertThat(lock.getCreatedAt()).isEqualTo(timestamp);
    }

    @Test
    void shouldSetAndGetExpiresAt() {
        AnalysisLock lock = new AnalysisLock();
        OffsetDateTime expiration = OffsetDateTime.now().plusMinutes(30);
        
        lock.setExpiresAt(expiration);
        
        assertThat(lock.getExpiresAt()).isEqualTo(expiration);
    }

    @Test
    void shouldSetAndGetCommitHash() {
        AnalysisLock lock = new AnalysisLock();
        String commitHash = "abc123def456";
        
        lock.setCommitHash(commitHash);
        
        assertThat(lock.getCommitHash()).isEqualTo(commitHash);
    }

    @Test
    void shouldSetAndGetPrNumber() {
        AnalysisLock lock = new AnalysisLock();
        lock.setPrNumber(42L);
        
        assertThat(lock.getPrNumber()).isEqualTo(42L);
    }

    @Test
    void shouldReturnFalseWhenNotExpired() {
        AnalysisLock lock = new AnalysisLock();
        lock.setExpiresAt(OffsetDateTime.now().plusMinutes(10));
        
        assertThat(lock.isExpired()).isFalse();
    }

    @Test
    void shouldReturnTrueWhenExpired() {
        AnalysisLock lock = new AnalysisLock();
        lock.setExpiresAt(OffsetDateTime.now().minusMinutes(10));
        
        assertThat(lock.isExpired()).isTrue();
    }

    @Test
    void shouldReturnTrueWhenExpiresAtIsNow() {
        AnalysisLock lock = new AnalysisLock();
        lock.setExpiresAt(OffsetDateTime.now().minusSeconds(1));
        
        assertThat(lock.isExpired()).isTrue();
    }

    @Test
    void shouldSetAllFieldsForPullRequestLock() {
        AnalysisLock lock = new AnalysisLock();
        Project project = new Project();
        OffsetDateTime now = OffsetDateTime.now();
        OffsetDateTime expiry = now.plusMinutes(15);
        
        lock.setProject(project);
        lock.setBranchName("feature/user-auth");
        lock.setAnalysisType(AnalysisLockType.PR_ANALYSIS);
        lock.setLockKey("lock:999:feature/user-auth:PR");
        lock.setOwnerInstanceId("server-01");
        lock.setCreatedAt(now);
        lock.setExpiresAt(expiry);
        lock.setCommitHash("deadbeef");
        lock.setPrNumber(789L);
        
        assertThat(lock.getProject()).isEqualTo(project);
        assertThat(lock.getBranchName()).isEqualTo("feature/user-auth");
        assertThat(lock.getAnalysisType()).isEqualTo(AnalysisLockType.PR_ANALYSIS);
        assertThat(lock.getLockKey()).isEqualTo("lock:999:feature/user-auth:PR");
        assertThat(lock.getOwnerInstanceId()).isEqualTo("server-01");
        assertThat(lock.getCreatedAt()).isEqualTo(now);
        assertThat(lock.getExpiresAt()).isEqualTo(expiry);
        assertThat(lock.getCommitHash()).isEqualTo("deadbeef");
        assertThat(lock.getPrNumber()).isEqualTo(789L);
        assertThat(lock.isExpired()).isFalse();
    }

    @Test
    void shouldSetAllFieldsForBranchLock() {
        AnalysisLock lock = new AnalysisLock();
        Project project = new Project();
        
        lock.setProject(project);
        lock.setBranchName("main");
        lock.setAnalysisType(AnalysisLockType.BRANCH_ANALYSIS);
        lock.setLockKey("lock:111:main:BRANCH");
        lock.setOwnerInstanceId("worker-05");
        lock.setExpiresAt(OffsetDateTime.now().plusHours(1));
        lock.setCommitHash("fedcba98");
        lock.setPrNumber(null);
        
        assertThat(lock.getAnalysisType()).isEqualTo(AnalysisLockType.BRANCH_ANALYSIS);
        assertThat(lock.getPrNumber()).isNull();
        assertThat(lock.isExpired()).isFalse();
    }
}

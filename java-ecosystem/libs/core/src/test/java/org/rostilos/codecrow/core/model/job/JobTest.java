package org.rostilos.codecrow.core.model.job;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.user.User;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("Job")
class JobTest {

    private Job job;

    @BeforeEach
    void setUp() {
        job = new Job();
    }

    @Nested
    @DisplayName("Default Values")
    class DefaultValues {

        @Test
        @DisplayName("should have UUID externalId by default")
        void shouldHaveExternalIdByDefault() {
            assertThat(job.getExternalId()).isNotNull();
            assertThat(job.getExternalId()).hasSize(36); // UUID format
        }

        @Test
        @DisplayName("should have PENDING status by default")
        void shouldHavePendingStatusByDefault() {
            assertThat(job.getStatus()).isEqualTo(JobStatus.PENDING);
        }

        @Test
        @DisplayName("should have progress 0 by default")
        void shouldHaveProgressZeroByDefault() {
            assertThat(job.getProgress()).isEqualTo(0);
        }

        @Test
        @DisplayName("should have createdAt set by default")
        void shouldHaveCreatedAtByDefault() {
            assertThat(job.getCreatedAt()).isNotNull();
        }

        @Test
        @DisplayName("should have updatedAt set by default")
        void shouldHaveUpdatedAtByDefault() {
            assertThat(job.getUpdatedAt()).isNotNull();
        }

        @Test
        @DisplayName("should have empty logs list by default")
        void shouldHaveEmptyLogsListByDefault() {
            assertThat(job.getLogs()).isEmpty();
        }
    }

    @Nested
    @DisplayName("Getters and Setters")
    class GettersSetters {

        @Test
        @DisplayName("should set and get externalId")
        void shouldSetAndGetExternalId() {
            job.setExternalId("custom-id");
            assertThat(job.getExternalId()).isEqualTo("custom-id");
        }

        @Test
        @DisplayName("should set and get project")
        void shouldSetAndGetProject() {
            Project project = new Project();
            job.setProject(project);
            assertThat(job.getProject()).isSameAs(project);
        }

        @Test
        @DisplayName("should set and get triggeredBy user")
        void shouldSetAndGetTriggeredBy() {
            User user = new User();
            job.setTriggeredBy(user);
            assertThat(job.getTriggeredBy()).isSameAs(user);
        }

        @Test
        @DisplayName("should set and get jobType")
        void shouldSetAndGetJobType() {
            job.setJobType(JobType.PR_ANALYSIS);
            assertThat(job.getJobType()).isEqualTo(JobType.PR_ANALYSIS);
        }

        @Test
        @DisplayName("should set and get status")
        void shouldSetAndGetStatus() {
            job.setStatus(JobStatus.RUNNING);
            assertThat(job.getStatus()).isEqualTo(JobStatus.RUNNING);
        }

        @Test
        @DisplayName("should set and get triggerSource")
        void shouldSetAndGetTriggerSource() {
            job.setTriggerSource(JobTriggerSource.WEBHOOK);
            assertThat(job.getTriggerSource()).isEqualTo(JobTriggerSource.WEBHOOK);
        }

        @Test
        @DisplayName("should set and get title")
        void shouldSetAndGetTitle() {
            job.setTitle("Test Job");
            assertThat(job.getTitle()).isEqualTo("Test Job");
        }

        @Test
        @DisplayName("should set and get branchName")
        void shouldSetAndGetBranchName() {
            job.setBranchName("feature/test");
            assertThat(job.getBranchName()).isEqualTo("feature/test");
        }

        @Test
        @DisplayName("should set and get prNumber")
        void shouldSetAndGetPrNumber() {
            job.setPrNumber(123L);
            assertThat(job.getPrNumber()).isEqualTo(123L);
        }

        @Test
        @DisplayName("should set and get commitHash")
        void shouldSetAndGetCommitHash() {
            job.setCommitHash("abc123def456");
            assertThat(job.getCommitHash()).isEqualTo("abc123def456");
        }

        @Test
        @DisplayName("should set and get codeAnalysis")
        void shouldSetAndGetCodeAnalysis() {
            CodeAnalysis analysis = new CodeAnalysis();
            job.setCodeAnalysis(analysis);
            assertThat(job.getCodeAnalysis()).isSameAs(analysis);
        }

        @Test
        @DisplayName("should set and get errorMessage")
        void shouldSetAndGetErrorMessage() {
            job.setErrorMessage("Something went wrong");
            assertThat(job.getErrorMessage()).isEqualTo("Something went wrong");
        }

        @Test
        @DisplayName("should set and get progress")
        void shouldSetAndGetProgress() {
            job.setProgress(50);
            assertThat(job.getProgress()).isEqualTo(50);
        }

        @Test
        @DisplayName("should set and get currentStep")
        void shouldSetAndGetCurrentStep() {
            job.setCurrentStep("Analyzing files");
            assertThat(job.getCurrentStep()).isEqualTo("Analyzing files");
        }

        @Test
        @DisplayName("should set and get startedAt")
        void shouldSetAndGetStartedAt() {
            OffsetDateTime now = OffsetDateTime.now();
            job.setStartedAt(now);
            assertThat(job.getStartedAt()).isEqualTo(now);
        }

        @Test
        @DisplayName("should set and get completedAt")
        void shouldSetAndGetCompletedAt() {
            OffsetDateTime now = OffsetDateTime.now();
            job.setCompletedAt(now);
            assertThat(job.getCompletedAt()).isEqualTo(now);
        }
    }

    @Nested
    @DisplayName("start()")
    class StartTests {

        @Test
        @DisplayName("should set status to RUNNING")
        void shouldSetStatusToRunning() {
            job.start();
            assertThat(job.getStatus()).isEqualTo(JobStatus.RUNNING);
        }

        @Test
        @DisplayName("should set startedAt")
        void shouldSetStartedAt() {
            job.start();
            assertThat(job.getStartedAt()).isNotNull();
        }
    }

    @Nested
    @DisplayName("complete()")
    class CompleteTests {

        @Test
        @DisplayName("should set status to COMPLETED")
        void shouldSetStatusToCompleted() {
            job.complete();
            assertThat(job.getStatus()).isEqualTo(JobStatus.COMPLETED);
        }

        @Test
        @DisplayName("should set completedAt")
        void shouldSetCompletedAt() {
            job.complete();
            assertThat(job.getCompletedAt()).isNotNull();
        }

        @Test
        @DisplayName("should set progress to 100")
        void shouldSetProgressTo100() {
            job.complete();
            assertThat(job.getProgress()).isEqualTo(100);
        }
    }

    @Nested
    @DisplayName("fail()")
    class FailTests {

        @Test
        @DisplayName("should set status to FAILED")
        void shouldSetStatusToFailed() {
            job.fail("Error occurred");
            assertThat(job.getStatus()).isEqualTo(JobStatus.FAILED);
        }

        @Test
        @DisplayName("should set completedAt")
        void shouldSetCompletedAt() {
            job.fail("Error occurred");
            assertThat(job.getCompletedAt()).isNotNull();
        }

        @Test
        @DisplayName("should set errorMessage")
        void shouldSetErrorMessage() {
            job.fail("Something went wrong");
            assertThat(job.getErrorMessage()).isEqualTo("Something went wrong");
        }
    }

    @Nested
    @DisplayName("cancel()")
    class CancelTests {

        @Test
        @DisplayName("should set status to CANCELLED")
        void shouldSetStatusToCancelled() {
            job.cancel();
            assertThat(job.getStatus()).isEqualTo(JobStatus.CANCELLED);
        }

        @Test
        @DisplayName("should set completedAt")
        void shouldSetCompletedAt() {
            job.cancel();
            assertThat(job.getCompletedAt()).isNotNull();
        }
    }

    @Nested
    @DisplayName("skip()")
    class SkipTests {

        @Test
        @DisplayName("should set status to SKIPPED")
        void shouldSetStatusToSkipped() {
            job.skip("Not needed");
            assertThat(job.getStatus()).isEqualTo(JobStatus.SKIPPED);
        }

        @Test
        @DisplayName("should set completedAt")
        void shouldSetCompletedAt() {
            job.skip("Not needed");
            assertThat(job.getCompletedAt()).isNotNull();
        }

        @Test
        @DisplayName("should set errorMessage as reason")
        void shouldSetErrorMessageAsReason() {
            job.skip("Branch not configured");
            assertThat(job.getErrorMessage()).isEqualTo("Branch not configured");
        }

        @Test
        @DisplayName("should set progress to 100")
        void shouldSetProgressTo100() {
            job.skip("Not needed");
            assertThat(job.getProgress()).isEqualTo(100);
        }
    }

    @Nested
    @DisplayName("isTerminal()")
    class IsTerminalTests {

        @Test
        @DisplayName("should return false for PENDING")
        void shouldReturnFalseForPending() {
            job.setStatus(JobStatus.PENDING);
            assertThat(job.isTerminal()).isFalse();
        }

        @Test
        @DisplayName("should return false for RUNNING")
        void shouldReturnFalseForRunning() {
            job.setStatus(JobStatus.RUNNING);
            assertThat(job.isTerminal()).isFalse();
        }

        @Test
        @DisplayName("should return true for COMPLETED")
        void shouldReturnTrueForCompleted() {
            job.setStatus(JobStatus.COMPLETED);
            assertThat(job.isTerminal()).isTrue();
        }

        @Test
        @DisplayName("should return true for FAILED")
        void shouldReturnTrueForFailed() {
            job.setStatus(JobStatus.FAILED);
            assertThat(job.isTerminal()).isTrue();
        }

        @Test
        @DisplayName("should return true for CANCELLED")
        void shouldReturnTrueForCancelled() {
            job.setStatus(JobStatus.CANCELLED);
            assertThat(job.isTerminal()).isTrue();
        }

        @Test
        @DisplayName("should return true for SKIPPED")
        void shouldReturnTrueForSkipped() {
            job.setStatus(JobStatus.SKIPPED);
            assertThat(job.isTerminal()).isTrue();
        }
    }

    @Nested
    @DisplayName("addLog()")
    class AddLogTests {

        @Test
        @DisplayName("should add log with level and message")
        void shouldAddLogWithLevelAndMessage() {
            JobLog log = job.addLog(JobLogLevel.INFO, "Starting analysis");
            
            assertThat(job.getLogs()).hasSize(1);
            assertThat(log.getJob()).isSameAs(job);
            assertThat(log.getLevel()).isEqualTo(JobLogLevel.INFO);
            assertThat(log.getMessage()).isEqualTo("Starting analysis");
        }

        @Test
        @DisplayName("should add log with level, step and message")
        void shouldAddLogWithLevelStepAndMessage() {
            JobLog log = job.addLog(JobLogLevel.DEBUG, "parsing", "Processing file.java");
            
            assertThat(job.getLogs()).hasSize(1);
            assertThat(log.getJob()).isSameAs(job);
            assertThat(log.getLevel()).isEqualTo(JobLogLevel.DEBUG);
            assertThat(log.getStep()).isEqualTo("parsing");
            assertThat(log.getMessage()).isEqualTo("Processing file.java");
        }

        @Test
        @DisplayName("should add multiple logs")
        void shouldAddMultipleLogs() {
            job.addLog(JobLogLevel.INFO, "Log 1");
            job.addLog(JobLogLevel.WARN, "Log 2");
            job.addLog(JobLogLevel.ERROR, "Log 3");
            
            assertThat(job.getLogs()).hasSize(3);
        }
    }

    @Nested
    @DisplayName("onUpdate()")
    class OnUpdateTests {

        @Test
        @DisplayName("should update updatedAt timestamp")
        void shouldUpdateTimestamp() {
            OffsetDateTime original = job.getUpdatedAt();
            
            try { Thread.sleep(10); } catch (InterruptedException e) {}
            
            job.onUpdate();
            
            assertThat(job.getUpdatedAt()).isAfterOrEqualTo(original);
        }
    }

    @Nested
    @DisplayName("setLogs()")
    class SetLogsTests {

        @Test
        @DisplayName("should replace logs list")
        void shouldReplaceLogs() {
            job.addLog(JobLogLevel.INFO, "Old log");
            
            java.util.List<JobLog> newLogs = new java.util.ArrayList<>();
            JobLog newLog = new JobLog();
            newLog.setMessage("New log");
            newLogs.add(newLog);
            
            job.setLogs(newLogs);
            
            assertThat(job.getLogs()).hasSize(1);
            assertThat(job.getLogs().get(0).getMessage()).isEqualTo("New log");
        }
    }
}

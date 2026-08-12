package org.rostilos.codecrow.core.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobLog;
import org.rostilos.codecrow.core.model.job.JobStatus;
import org.rostilos.codecrow.core.persistence.repository.job.JobLogRepository;
import org.rostilos.codecrow.core.persistence.repository.job.JobRepository;
import org.springframework.data.domain.Pageable;
import org.springframework.test.util.ReflectionTestUtils;

import java.time.OffsetDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class JobServiceLegacyRagRecoveryTest {
    @Mock private JobRepository jobs;
    @Mock private JobLogRepository logs;

    private JobService service;

    @BeforeEach
    void setUp() {
        service = new JobService(jobs, logs, new ObjectMapper());
    }

    @Test
    void renewsTheExactLeaseWindowProvidedByTheProducer() {
        OffsetDateTime validAfter = OffsetDateTime.now().minusMinutes(2);
        OffsetDateTime renewedAt = OffsetDateTime.now();
        when(jobs.renewLegacyRagJobLease(91L, validAfter, renewedAt))
                .thenReturn(1);

        assertThat(service.renewLegacyRagJobLease(
                91L, validAfter, renewedAt)).isTrue();
    }

    @Test
    void exposesOnlyTheBoundedRecoveryBatch() {
        JobRepository.LegacyRagJobRecoveryCoordinates coordinates =
                org.mockito.Mockito.mock(
                        JobRepository.LegacyRagJobRecoveryCoordinates.class);
        when(jobs.findAbandonedLegacyRagJobs(
                any(OffsetDateTime.class), any(Pageable.class)))
                .thenReturn(List.of(coordinates));

        assertThat(service.findAbandonedLegacyRagJobs(
                OffsetDateTime.now(), 17)).containsExactly(coordinates);
        verify(jobs).findAbandonedLegacyRagJobs(
                any(OffsetDateTime.class), argThat(page -> page.getPageSize() == 17));
    }

    @Test
    void reportsWhetherRecoveryWonTheAtomicFence() {
        when(jobs.failAbandonedLegacyRagJob(
                eq(91L), any(OffsetDateTime.class), any(), eq("abandoned")))
                .thenReturn(1);

        assertThat(service.failAbandonedLegacyRagJob(
                91L, OffsetDateTime.now(), "abandoned")).isTrue();
    }

    @Test
    void recordsAnAlreadyCompletedJobWithoutRepeatingItsTerminalTransition() {
        Job supplied = new Job();
        ReflectionTestUtils.setField(supplied, "id", 91L);
        Job persisted = new Job();
        ReflectionTestUtils.setField(persisted, "id", 91L);
        persisted.setExternalId("job-91");
        persisted.setStatus(JobStatus.COMPLETED);
        when(jobs.findByIdForUpdate(91L)).thenReturn(java.util.Optional.of(persisted));
        when(logs.existsByJobIdAndStep(91L, "rag_complete")).thenReturn(false);
        when(logs.getNextSequenceNumber(91L)).thenReturn(4L);
        when(logs.save(any(JobLog.class))).thenAnswer(invocation -> invocation.getArgument(0));

        service.recordExternallyCompletedJob(
                supplied, "rag_complete", "RAG index updated");

        verify(logs).save(argThat(entry ->
                entry.getJob() == persisted
                        && "rag_complete".equals(entry.getStep())
                        && "RAG index updated".equals(entry.getMessage())));
        verify(jobs, never()).save(any());
    }

    @Test
    void repeatedTerminalNotificationDoesNotDuplicateTheDurableLog() {
        Job supplied = new Job();
        ReflectionTestUtils.setField(supplied, "id", 91L);
        Job persisted = new Job();
        ReflectionTestUtils.setField(persisted, "id", 91L);
        persisted.setExternalId("job-91");
        persisted.setStatus(JobStatus.COMPLETED);
        when(jobs.findByIdForUpdate(91L)).thenReturn(java.util.Optional.of(persisted));
        when(logs.existsByJobIdAndStep(91L, "rag_complete")).thenReturn(true);

        service.recordExternallyCompletedJob(
                supplied, "rag_complete", "RAG index updated");

        verify(logs, never()).save(any());
        verify(jobs, never()).save(any());
    }

    @Test
    void recordsAnAlreadyFailedJobWithoutRepeatingItsTerminalTransition() {
        Job supplied = new Job();
        ReflectionTestUtils.setField(supplied, "id", 91L);
        Job persisted = new Job();
        ReflectionTestUtils.setField(persisted, "id", 91L);
        persisted.setExternalId("job-91");
        persisted.setStatus(JobStatus.FAILED);
        when(jobs.findByIdForUpdate(91L))
                .thenReturn(java.util.Optional.of(persisted));
        when(logs.existsByJobIdAndStep(91L, "rag_recovery")).thenReturn(false);
        when(logs.getNextSequenceNumber(91L)).thenReturn(4L);
        when(logs.save(any(JobLog.class))).thenAnswer(invocation -> invocation.getArgument(0));

        service.recordExternallyFailedJob(
                supplied, "rag_recovery", "Job failed: producer expired");

        verify(logs).save(argThat(entry ->
                entry.getJob() == persisted
                        && entry.getLevel()
                            == org.rostilos.codecrow.core.model.job.JobLogLevel.ERROR
                        && "rag_recovery".equals(entry.getStep())));
        verify(jobs, never()).save(any());
    }
}

package org.rostilos.codecrow.core.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.rostilos.codecrow.core.model.job.*;
import org.rostilos.codecrow.core.persistence.repository.job.*;
import org.springframework.test.util.ReflectionTestUtils;
import java.time.OffsetDateTime;
import java.util.Optional;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class QaJobLifecycleTest {
    @ParameterizedTest @ValueSource(strings={"complete", "fail", "skip"})
    void staleQaWorkerCannotOverwriteNewerExecution(String transition) {
        var repository = mock(JobRepository.class); var logs = mock(JobLogRepository.class);
        var service = new JobService(repository, logs, new ObjectMapper());
        var stale = new Job(); var current = new Job();
        for (var job : new Job[]{stale, current}) {
            ReflectionTestUtils.setField(job, "id", 1L); job.setJobType(JobType.QA_DOC_COMMAND); job.setStatus(JobStatus.RUNNING);
        }
        stale.setStartedAt(OffsetDateTime.now().minusMinutes(40)); current.setStartedAt(OffsetDateTime.now());
        when(repository.findById(1L)).thenReturn(Optional.of(current));
        when(repository.findByIdForUpdate(1L)).thenReturn(Optional.of(current));
        when(repository.save(any())).thenAnswer(i -> i.getArgument(0));
        switch (transition) {
            case "complete" -> service.completeJob(stale);
            case "fail" -> service.failJob(stale, "old failure");
            case "skip" -> service.skipJob(stale, "old skip");
        }
        assertThat(current.getStatus()).isEqualTo(JobStatus.RUNNING);
        verify(repository, never()).save(any()); verifyNoInteractions(logs);
    }
}

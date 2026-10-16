package org.rostilos.codecrow.pipelineagent.qadoc;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.job.*;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.analysis.AnalysisLockRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.job.*;
import org.rostilos.codecrow.core.service.JobService;
import java.time.OffsetDateTime;
import java.util.Optional;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.any;

class QaDocQueueServiceTest {
    @Test void sourceReviewLeaseDefersDispatchWithoutChangingTheQueuedReceipt() {
        var analyses = mock(CodeAnalysisRepository.class);
        var repository = mock(JobRepository.class);
        var logs = mock(JobLogRepository.class);
        var locks = mock(AnalysisLockRepository.class);
        var service = new QaDocQueueService(analyses, repository, logs, locks, mock(JobService.class));
        var now = OffsetDateTime.now();
        var job = mock(Job.class); var project = mock(Project.class); var analysis = mock(CodeAnalysis.class);
        when(repository.findByIdForUpdate(7L)).thenReturn(Optional.of(job));
        when(job.getJobType()).thenReturn(JobType.QA_DOC_COMMAND);
        when(job.getTriggerSource()).thenReturn(JobTriggerSource.CHAINED);
        when(job.getStatus()).thenReturn(JobStatus.QUEUED);
        when(job.getUpdatedAt()).thenReturn(now.minusHours(1));
        when(job.getProject()).thenReturn(project); when(project.getId()).thenReturn(1L);
        when(job.getInputAnalysisId()).thenReturn(42L);
        when(analyses.findByIdAndProjectId(42L, 1L)).thenReturn(Optional.of(analysis));
        when(analysis.getSourceBranchName()).thenReturn("feature");
        when(locks.existsActiveLock(1L, "feature", AnalysisLockType.PR_ANALYSIS, now)).thenReturn(true);
        assertThat(service.prepareRetry(7L, now)).isFalse();
        verify(repository, never()).save(any());
        verifyNoInteractions(logs);
    }
}

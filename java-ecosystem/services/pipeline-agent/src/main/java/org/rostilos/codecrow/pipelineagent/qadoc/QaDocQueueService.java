package org.rostilos.codecrow.pipelineagent.qadoc;

import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.job.*;
import org.rostilos.codecrow.core.persistence.repository.analysis.AnalysisLockRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.job.*;
import org.rostilos.codecrow.core.service.JobService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.time.OffsetDateTime;
import java.util.Optional;

@Service
public class QaDocQueueService {
    @org.springframework.beans.factory.annotation.Autowired
    private org.rostilos.codecrow.core.service.ProjectFeaturePolicy featurePolicy =
            new org.rostilos.codecrow.core.service.ProjectFeaturePolicy();

    private final CodeAnalysisRepository analyses;
    private final JobRepository jobs;
    private final JobLogRepository logs;
    private final AnalysisLockRepository locks;
    private final JobService jobService;
    public QaDocQueueService(CodeAnalysisRepository analyses, JobRepository jobs, JobLogRepository logs,
                             AnalysisLockRepository locks, JobService jobService) {
        this.analyses=analyses; this.jobs=jobs; this.logs=logs; this.locks=locks; this.jobService=jobService;
    }

    @Transactional
    public Optional<Job> enqueue(Long projectId, Long analysisId) {
        var analysis=analyses.findForQaQueue(projectId,analysisId).orElse(null);
        if (analysis == null || !analysis.isQaDocumentationPending()) return Optional.empty();
        if (analysis.getAnalysisType() != AnalysisType.PR_REVIEW || analysis.getPrNumber() == null) return Optional.empty();
        if (!featurePolicy.qaDocumentationEnabled(analysis.getProject())) {
            analyses.consumeQaIntent(projectId, analysisId);
            org.slf4j.LoggerFactory.getLogger(getClass()).info("QA documentation skipped by host capability: project={}, analysis={}", projectId, analysisId);
            return Optional.empty();
        }
        var job=jobService.createCommandJob(analysis.getProject(),JobType.QA_DOC_COMMAND,
                analysis.getPrNumber(),analysis.getCommitHash(),JobTriggerSource.CHAINED);
        job.setInputAnalysisId(analysis.getId()); jobs.save(job);
        job=jobService.queueWebhookJob(job,"{\"analysisId\":" + analysis.getId() + "}");
        analyses.consumeQaIntent(projectId,analysisId);
        return Optional.of(job);
    }

    @Transactional
    public boolean prepareRetry(Long jobId, OffsetDateTime now) {
        var job=jobs.findByIdForUpdate(jobId).orElse(null);
        if (job == null || job.getJobType() != JobType.QA_DOC_COMMAND) return false;
        boolean abandoned=job.getStatus() == JobStatus.RUNNING && job.getUpdatedAt().isBefore(now.minusMinutes(30));
        boolean failed=job.getStatus() == JobStatus.FAILED && job.getUpdatedAt().isBefore(now.minusMinutes(1));
        boolean queued=(job.getStatus() == JobStatus.PENDING || job.getStatus() == JobStatus.QUEUED)
                && job.getUpdatedAt().isBefore(now.minusSeconds(30));
        if (!queued && !abandoned && !failed) return false;
        if (job.getTriggerSource() == JobTriggerSource.CHAINED && job.getInputAnalysisId() != null) {
            var source = analyses.findByIdAndProjectId(job.getInputAnalysisId(), job.getProject().getId()).orElse(null);
            if (source != null && source.getSourceBranchName() != null
                    && locks.existsActiveLock(job.getProject().getId(), source.getSourceBranchName(),
                            AnalysisLockType.PR_ANALYSIS, now)) return false;
        }
        if (abandoned && locks.existsActiveLock(job.getProject().getId(),"qa-pr:" + job.getPrNumber(),
                AnalysisLockType.QA_DOCUMENTATION,now)) return false;
        int attempts=logs.findByJobIdAndStep(jobId,"qa_attempt").size();
        if (attempts >= 3) {
            if (abandoned) {
                job.fail("QA worker disappeared after three attempts; retry the QA command to resume the saved document");
                jobs.save(job);
                var entry=new JobLog(); entry.setJob(job); entry.setLevel(JobLogLevel.ERROR);
                entry.setStep("qa_exhausted"); entry.setMessage(job.getErrorMessage());
                entry.setSequenceNumber(logs.getNextSequenceNumber(jobId)); logs.save(entry);
            }
            return false;
        }
        job.setStatus(JobStatus.QUEUED); job.setStartedAt(null); job.setCompletedAt(null);
        job.setErrorMessage(null); job.setCurrentStep("Recovering persisted QA work"); jobs.save(job);
        return true;
    }
}

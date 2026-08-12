package org.rostilos.codecrow.ragengine.branch;

import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexKind;
import org.rostilos.codecrow.core.service.AnalysisJobService;
import org.rostilos.codecrow.ragengine.service.RagBranchIndexRegistryService;
import org.rostilos.codecrow.ragengine.service.RagIndexTrackingService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

/**
 * Commits the durable ownership boundary for an exact branch snapshot.
 *
 * <p>The RAG operation is registered before its child job is created. The job
 * and operation are then linked and moved to RUNNING in this one transaction,
 * so recovery can never observe a committed running exact-generation job
 * without a corresponding operation.</p>
 */
@Service
public class BranchIndexBuildAdmissionService {

    public enum BuildOrigin {
        AUTOMATIC("automatic"),
        OPERATOR("operator");

        private final String fingerprintLabel;

        BuildOrigin(String fingerprintLabel) {
            this.fingerprintLabel = fingerprintLabel;
        }
    }

    public enum ProjectStatusAdmission {
        NONE,
        INDEXING,
        UPDATING
    }

    public record AdmittedBuild(
            Job job,
            BranchIndexGenerationBuildService.PreparedBuild preparedBuild,
            ProjectStatusAdmission statusAdmission) {
    }

    private final RagBranchIndexRegistryService registryService;
    private final AnalysisJobService jobService;
    private final RagIndexTrackingService trackingService;

    public BranchIndexBuildAdmissionService(
            RagBranchIndexRegistryService registryService,
            AnalysisJobService jobService,
            RagIndexTrackingService trackingService) {
        this.registryService = registryService;
        this.jobService = jobService;
        this.trackingService = trackingService;
    }

    @Transactional
    public AdmittedBuild admit(
            Project project,
            String branch,
            String revision,
            RagBranchIndexKind kind,
            JobTriggerSource triggerSource,
            String analysisLockKey,
            BuildOrigin origin) {
        String lockKey = requireText(analysisLockKey, "analysisLockKey");
        BuildOrigin buildOrigin = origin != null ? origin : BuildOrigin.AUTOMATIC;

        var registration = registryService.registerBuild(
                project,
                branch,
                kind,
                null,
                revision,
                operationFingerprint(buildOrigin, lockKey));
        if (registration.existingOperation()) {
            // A lock key identifies one acquisition. Seeing it again means a
            // previous admission committed and recovery already owns that
            // operation; creating a second child job would split ownership.
            throw new IllegalStateException(
                    "Exact RAG build for this lock was already admitted");
        }

        BranchIndexGenerationBuildService.PreparedBuild prepared =
                BranchIndexGenerationBuildService.prepare(registration, lockKey);
        var activeSource = registration.generation().getParentGeneration();
        ProjectStatusAdmission projectStatus = kind != RagBranchIndexKind.PRIMARY
                ? ProjectStatusAdmission.NONE
                : (activeSource == null
                    ? ProjectStatusAdmission.INDEXING
                    : ProjectStatusAdmission.UPDATING);

        if (projectStatus == ProjectStatusAdmission.UPDATING) {
            // The active exact generation is the authoritative completed
            // checkpoint. Align a stale/failed legacy status under the branch
            // lock before switching it to UPDATING, all in this transaction.
            trackingService.preparePublishedGenerationForUpdate(
                    project,
                    branch,
                    activeSource.getRevision(),
                    activeSource.getFileCount(),
                    activeSource.getChunkCount());
        }
        Job job = jobService.createRagIndexJob(
                project,
                projectStatus == ProjectStatusAdmission.INDEXING,
                triggerSource,
                branch,
                revision);
        if (job == null || job.getId() == null) {
            throw new IllegalStateException(
                    "A durable branch-bound RAG job could not be created");
        }

        // Attach the operation before starting the job. The surrounding
        // transaction commits both state transitions together.
        registryService.startBuild(prepared.operationId(), job.getId(), lockKey);
        jobService.startJob(job);
        if (projectStatus == ProjectStatusAdmission.INDEXING) {
            trackingService.markIndexingStarted(
                    project, branch, revision, job.getId());
        } else if (projectStatus == ProjectStatusAdmission.UPDATING) {
            trackingService.markUpdatingStarted(
                    project, branch, revision, job.getId());
        }
        return new AdmittedBuild(job, prepared, projectStatus);
    }

    /**
     * Immediately terminalizes an admitted build that cannot reach execute.
     * This is only for failures in the narrow local hand-off after admission;
     * failures inside execute are owned by the build service itself.
     */
    public void abortOperation(AdmittedBuild admission, String diagnostic) {
        if (admission == null) {
            return;
        }
        String failure = diagnostic == null || diagnostic.isBlank()
                ? "Exact RAG build failed before execution"
                : diagnostic;
        registryService.fail(admission.preparedBuild().operationId(), failure);
    }

    static String operationFingerprint(BuildOrigin origin, String analysisLockKey) {
        return "exact-full-snapshot:" + origin.fingerprintLabel + ":"
                + digest(requireText(analysisLockKey, "analysisLockKey"));
    }

    private static String requireText(String value, String field) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(field + " is required");
        }
        return value.trim();
    }

    private static String digest(String value) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256")
                            .digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException failure) {
            throw new IllegalStateException("SHA-256 is unavailable", failure);
        }
    }
}

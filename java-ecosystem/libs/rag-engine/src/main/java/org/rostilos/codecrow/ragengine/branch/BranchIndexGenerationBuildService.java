package org.rostilos.codecrow.ragengine.branch;

import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.BranchArchiveService;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexKind;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexGenerationStatus;
import org.rostilos.codecrow.core.model.rag.RagIndexOperationStatus;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
import org.rostilos.codecrow.ragengine.service.RagBranchIndexRegistryService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

/**
 * Builds one exact branch generation without depending on analysis processors.
 * VCS acquisition, vector storage, and registry publication meet only through
 * this small orchestration boundary.
 */
@Service
public class BranchIndexGenerationBuildService {
    private static final Logger log = LoggerFactory.getLogger(
            BranchIndexGenerationBuildService.class);
    private final BranchArchiveService archiveService;
    private final RagPipelineClient pipelineClient;
    private final RagBranchIndexRegistryService registryService;
    private final RagIndexOperationHeartbeatService heartbeatService;
    private final AnalysisLockService analysisLockService;
    private final int ragLockLeaseMinutes;

    /**
     * The durable coordinates needed after build admission commits. Keeping
     * this boundary scalar prevents callers from carrying detached registry
     * entities (and their lazy associations) into archive or RAG I/O.
     */
    public record PreparedBuild(
            long operationId,
            String collectionTarget,
            boolean alreadySucceeded,
            String manifestDigest,
            String analysisLockKey,
            String sourceCollectionTarget) {

        public PreparedBuild(
                long operationId,
                String collectionTarget,
                boolean alreadySucceeded,
                String manifestDigest,
                String analysisLockKey) {
            this(operationId, collectionTarget, alreadySucceeded, manifestDigest,
                    analysisLockKey, null);
        }

        public PreparedBuild {
            if (operationId <= 0) {
                throw new IllegalArgumentException("operationId must be positive");
            }
            if (collectionTarget == null || collectionTarget.isBlank()) {
                throw new IllegalArgumentException("collectionTarget is required");
            }
        }
    }

    public BranchIndexGenerationBuildService(
            BranchArchiveService archiveService,
            RagPipelineClient pipelineClient,
            RagBranchIndexRegistryService registryService,
            RagIndexOperationHeartbeatService heartbeatService) {
        this(archiveService, pipelineClient, registryService, heartbeatService, null, 360);
    }

    @Autowired
    public BranchIndexGenerationBuildService(
            BranchArchiveService archiveService,
            RagPipelineClient pipelineClient,
            RagBranchIndexRegistryService registryService,
            RagIndexOperationHeartbeatService heartbeatService,
            AnalysisLockService analysisLockService,
            @Value("${analysis.lock.rag.timeout.minutes:360}") int ragLockLeaseMinutes) {
        this.archiveService = archiveService;
        this.pipelineClient = pipelineClient;
        this.registryService = registryService;
        this.heartbeatService = heartbeatService;
        this.analysisLockService = analysisLockService;
        this.ragLockLeaseMinutes = Math.max(1, ragLockLeaseMinutes);
    }

    public Map<String, Object> build(
            Project project,
            VcsConnection connection,
            String vcsWorkspace,
            String repoSlug,
            String branch,
            String revision,
            RagBranchIndexKind kind,
            List<String> includePatterns,
            List<String> excludePatterns) throws IOException {
        return build(project, connection, vcsWorkspace, repoSlug, branch,
                revision, kind, includePatterns, excludePatterns, null);
    }

    public Map<String, Object> build(
            Project project,
            VcsConnection connection,
            String vcsWorkspace,
            String repoSlug,
            String branch,
            String revision,
            RagBranchIndexKind kind,
            List<String> includePatterns,
            List<String> excludePatterns,
            Long jobId,
            Consumer<Map<String, Object>> progressEvents) throws IOException {
        return build(project, connection, vcsWorkspace, repoSlug, branch, revision,
                kind, includePatterns, excludePatterns, jobId, null, progressEvents);
    }

    public Map<String, Object> build(
            Project project,
            VcsConnection connection,
            String vcsWorkspace,
            String repoSlug,
            String branch,
            String revision,
            RagBranchIndexKind kind,
            List<String> includePatterns,
            List<String> excludePatterns,
            Long jobId,
            String analysisLockKey,
            Consumer<Map<String, Object>> progressEvents) throws IOException {
        return buildInternal(project, connection, vcsWorkspace, repoSlug, branch,
                revision, kind, includePatterns, excludePatterns, jobId,
                analysisLockKey, progressEvents, false);
    }

    /**
     * Builds a fresh immutable generation even when this revision already has
     * a successful generation. This is reserved for an explicit operator
     * refresh; automatic maintenance remains idempotent.
     */
    public Map<String, Object> rebuild(
            Project project,
            VcsConnection connection,
            String vcsWorkspace,
            String repoSlug,
            String branch,
            String revision,
            RagBranchIndexKind kind,
            List<String> includePatterns,
            List<String> excludePatterns,
            Long jobId,
            Consumer<Map<String, Object>> progressEvents) throws IOException {
        return rebuild(project, connection, vcsWorkspace, repoSlug, branch, revision,
                kind, includePatterns, excludePatterns, jobId, null, progressEvents);
    }

    public Map<String, Object> rebuild(
            Project project,
            VcsConnection connection,
            String vcsWorkspace,
            String repoSlug,
            String branch,
            String revision,
            RagBranchIndexKind kind,
            List<String> includePatterns,
            List<String> excludePatterns,
            Long jobId,
            String analysisLockKey,
            Consumer<Map<String, Object>> progressEvents) throws IOException {
        return buildInternal(project, connection, vcsWorkspace, repoSlug, branch,
                revision, kind, includePatterns, excludePatterns, jobId,
                analysisLockKey, progressEvents, true);
    }

    public Map<String, Object> build(
            Project project,
            VcsConnection connection,
            String vcsWorkspace,
            String repoSlug,
            String branch,
            String revision,
            RagBranchIndexKind kind,
            List<String> includePatterns,
            List<String> excludePatterns,
            Long jobId) throws IOException {
        return buildInternal(project, connection, vcsWorkspace, repoSlug, branch,
                revision, kind, includePatterns, excludePatterns, jobId, null, null, false);
    }

    private Map<String, Object> buildInternal(
            Project project,
            VcsConnection connection,
            String vcsWorkspace,
            String repoSlug,
            String branch,
            String revision,
            RagBranchIndexKind kind,
            List<String> includePatterns,
            List<String> excludePatterns,
            Long jobId,
            String analysisLockKey,
            Consumer<Map<String, Object>> progressEvents,
            boolean forceRebuild) throws IOException {
        var registration = registryService.registerBuild(
                project, branch, kind, null, revision,
                forceRebuild
                        ? "full-snapshot:job:" + requireJobId(jobId) + ":" + UUID.randomUUID()
                        : null);
        PreparedBuild prepared = prepare(registration, analysisLockKey);
        if (prepared.alreadySucceeded()) {
            return Map.of(
                    "status", "reused",
                    "collection_target", prepared.collectionTarget(),
                    "generation_manifest_sha256", prepared.manifestDigest());
        }

        registryService.startBuild(
                prepared.operationId(), jobId, analysisLockKey);
        return execute(project, connection, vcsWorkspace, repoSlug, branch, revision,
                kind, includePatterns, excludePatterns, prepared, progressEvents);
    }

    /**
     * Executes an already-admitted build. The operation must have been linked
     * to its durable job and transitioned to RUNNING in the admission
     * transaction before this method is called.
     */
    public Map<String, Object> execute(
            Project project,
            VcsConnection connection,
            String vcsWorkspace,
            String repoSlug,
            String branch,
            String revision,
            RagBranchIndexKind kind,
            List<String> includePatterns,
            List<String> excludePatterns,
            PreparedBuild prepared,
            Consumer<Map<String, Object>> progressEvents) throws IOException {
        if (prepared == null) {
            throw new IllegalArgumentException("prepared build is required");
        }
        if (prepared.alreadySucceeded()) {
            return Map.of(
                    "status", "reused",
                    "collection_target", prepared.collectionTarget(),
                    "generation_manifest_sha256", prepared.manifestDigest());
        }

        RagIndexOperationHeartbeatService.HeartbeatScope heartbeat = null;
        AnalysisLockService.LockLease lockLease = null;
        Path snapshot = null;
        AtomicBoolean snapshotOwnershipTransferred = new AtomicBoolean(false);
        try {
            lockLease = startAnalysisLockLease(prepared);
            heartbeat = heartbeatService.start(prepared.operationId());
            snapshot = Files.createTempDirectory("codecrow-rag-branch-generation-");
            archiveService.downloadAndExtractSnapshotToDirectory(
                    connection,
                    vcsWorkspace,
                    repoSlug,
                    revision,
                    null,
                    snapshot);
            boolean publishBranchAlias = kind == RagBranchIndexKind.PRIMARY
                    || kind == RagBranchIndexKind.DURABLE;
            boolean publishLegacyProjectAlias = kind == RagBranchIndexKind.PRIMARY;
            Map<String, Object> result;
            var analysisProfile = project.getEffectiveConfig().analysisProfile();
            String projectType = analysisProfile.projectType();
            String sourceRoot = analysisProfile.sourceRoot();
            boolean profileConfigured = projectType != null || sourceRoot != null;
            if (progressEvents == null) {
                if (prepared.sourceCollectionTarget() == null) {
                    result = profileConfigured
                            ? pipelineClient.indexRepository(
                                snapshot.toString(), project.getWorkspace().getName(),
                                project.getNamespace(), branch, revision, includePatterns,
                                excludePatterns, prepared.collectionTarget(),
                                false, false, null, projectType, sourceRoot)
                            : pipelineClient.indexRepository(
                                snapshot.toString(), project.getWorkspace().getName(),
                                project.getNamespace(), branch, revision, includePatterns,
                                excludePatterns, prepared.collectionTarget(),
                                false, false);
                } else {
                    result = profileConfigured
                            ? pipelineClient.indexRepository(
                                snapshot.toString(), project.getWorkspace().getName(),
                                project.getNamespace(), branch, revision, includePatterns,
                                excludePatterns, prepared.collectionTarget(),
                                false, false, prepared.sourceCollectionTarget(),
                                projectType, sourceRoot)
                            : pipelineClient.indexRepository(
                                snapshot.toString(), project.getWorkspace().getName(),
                                project.getNamespace(), branch, revision, includePatterns,
                                excludePatterns, prepared.collectionTarget(),
                                false, false, prepared.sourceCollectionTarget());
                }
            } else {
                if (prepared.sourceCollectionTarget() == null) {
                    result = profileConfigured
                            ? pipelineClient.indexRepository(
                                snapshot.toString(), project.getWorkspace().getName(),
                                project.getNamespace(), branch, revision, includePatterns,
                                excludePatterns, prepared.collectionTarget(),
                                false, false, true, null,
                                () -> snapshotOwnershipTransferred.set(true),
                                progressEvents, projectType, sourceRoot)
                            : pipelineClient.indexRepository(
                                snapshot.toString(), project.getWorkspace().getName(),
                                project.getNamespace(), branch, revision, includePatterns,
                                excludePatterns, prepared.collectionTarget(),
                                false, false, true,
                                () -> snapshotOwnershipTransferred.set(true),
                                progressEvents);
                } else {
                    result = profileConfigured
                            ? pipelineClient.indexRepository(
                                snapshot.toString(), project.getWorkspace().getName(),
                                project.getNamespace(), branch, revision, includePatterns,
                                excludePatterns, prepared.collectionTarget(),
                                false, false, true, prepared.sourceCollectionTarget(),
                                () -> snapshotOwnershipTransferred.set(true),
                                progressEvents, projectType, sourceRoot)
                            : pipelineClient.indexRepository(
                                snapshot.toString(), project.getWorkspace().getName(),
                                project.getNamespace(), branch, revision, includePatterns,
                                excludePatterns, prepared.collectionTarget(),
                                false, false, true, prepared.sourceCollectionTarget(),
                                () -> snapshotOwnershipTransferred.set(true),
                                progressEvents);
                }
            }
            Object manifest = result.get("generation_manifest_sha256");
            if (!(manifest instanceof String digest) || digest.isBlank()) {
                throw new IOException("RAG full branch generation has no manifest digest");
            }
            if (lockLease != null && !lockLease.confirmOwnership()) {
                throw new IOException(
                        "RAG indexing lock ownership was lost before generation publication");
            }
            var published = registryService.publish(
                    prepared.operationId(),
                    digest,
                    number(result.get("document_count")),
                    number(result.get("chunk_count")));
            publishReadableAliasesIfActive(
                    project, branch, revision, prepared.collectionTarget(),
                    published, publishBranchAlias, publishLegacyProjectAlias);
            return result;
        } catch (Throwable failure) {
            registryService.fail(
                    prepared.operationId(),
                    failure.getMessage() != null
                            ? failure.getMessage()
                            : failure.getClass().getSimpleName());
            if (failure instanceof IOException ioFailure) {
                throw ioFailure;
            }
            if (failure instanceof Error error) {
                throw error;
            }
            throw new IOException("Failed to build exact branch generation", failure);
        } finally {
            if (lockLease != null) {
                lockLease.close();
            }
            if (heartbeat != null) {
                heartbeat.close();
            }
            if (snapshot != null && !snapshotOwnershipTransferred.get()) {
                deleteTree(snapshot);
            }
        }
    }

    /** Maps a managed registration to the scalar post-transaction boundary. */
    public static PreparedBuild prepare(
            RagBranchIndexRegistryService.BuildRegistration registration) {
        return prepare(registration, null);
    }

    public static PreparedBuild prepare(
            RagBranchIndexRegistryService.BuildRegistration registration,
            String analysisLockKey) {
        if (registration == null || registration.operation() == null
                || registration.generation() == null) {
            throw new IllegalArgumentException("A complete build registration is required");
        }
        boolean succeeded = registration.existingOperation()
                && registration.operation().getStatus() == RagIndexOperationStatus.SUCCEEDED;
        return new PreparedBuild(
                registration.operation().getId(),
                registration.generation().getCollectionName(),
                succeeded,
                registration.generation().getManifestDigest(),
                analysisLockKey,
                registration.sourceCollectionTarget());
    }

    private AnalysisLockService.LockLease startAnalysisLockLease(PreparedBuild prepared)
            throws IOException {
        if (prepared.analysisLockKey() == null || prepared.analysisLockKey().isBlank()) {
            return null;
        }
        if (analysisLockService == null) {
            throw new IOException("Analysis-lock lease service is unavailable for exact RAG build");
        }
        AnalysisLockService.LockLease lease = analysisLockService.maintainLockLease(
                prepared.analysisLockKey(), ragLockLeaseMinutes);
        if (lease.isOwnershipLost()) {
            lease.close();
            throw new IOException("RAG indexing lock ownership was lost before snapshot build");
        }
        return lease;
    }

    private void publishReadableAliasesIfActive(
            Project project,
            String branch,
            String revision,
            String collectionTarget,
            org.rostilos.codecrow.core.model.rag.RagBranchIndexGeneration published,
            boolean publishBranchAlias,
            boolean publishLegacyProjectAlias) {
        if (published == null
                || published.getStatus() != RagBranchIndexGenerationStatus.ACTIVE
                || !publishBranchAlias) {
            return;
        }
        try {
            pipelineClient.publishGenerationAliases(
                    project.getWorkspace().getName(), project.getNamespace(),
                    branch, revision, collectionTarget,
                    published.getManifestDigest(),
                    true, publishLegacyProjectAlias);
        } catch (IOException | RuntimeException aliasFailure) {
            // Readable aliases are operator convenience. Exact retrieval uses
            // the registry target, and reconciliation repairs this alias later.
            log.info("Readable aliases were not published for active RAG generation {}; "
                            + "the reconciliation scheduler will retry: {}",
                    published.getId(), aliasFailure.getMessage());
        }
    }

    private static int number(Object value) {
        return value instanceof Number number ? number.intValue() : 0;
    }

    private static String requireJobId(Long jobId) {
        if (jobId == null) {
            throw new IllegalArgumentException("An operator refresh requires a durable job id");
        }
        return jobId.toString();
    }

    private static void deleteTree(Path root) {
        try {
            if (!Files.exists(root)) {
                return;
            }
            try (var paths = Files.walk(root)) {
                for (Path path : paths.sorted(Comparator.reverseOrder()).toList()) {
                    Files.deleteIfExists(path);
                }
            }
        } catch (IOException ignored) {
            // Temporary cleanup must not replace the indexing result.
        }
    }
}

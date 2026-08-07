package org.rostilos.codecrow.ragengine.branch;

import org.rostilos.codecrow.analysisengine.service.BranchArchiveService;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexKind;
import org.rostilos.codecrow.core.model.rag.RagIndexOperationStatus;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
import org.rostilos.codecrow.ragengine.service.RagBranchIndexRegistryService;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

/**
 * Builds one exact branch generation without depending on analysis processors.
 * VCS acquisition, vector storage, and registry publication meet only through
 * this small orchestration boundary.
 */
@Service
public class BranchIndexGenerationBuildService {
    private static final long HEARTBEAT_INTERVAL_SECONDS = 15;

    private final BranchArchiveService archiveService;
    private final RagPipelineClient pipelineClient;
    private final RagBranchIndexRegistryService registryService;
    private final ScheduledExecutorService heartbeatExecutor;

    public BranchIndexGenerationBuildService(
            BranchArchiveService archiveService,
            RagPipelineClient pipelineClient,
            RagBranchIndexRegistryService registryService) {
        this.archiveService = archiveService;
        this.pipelineClient = pipelineClient;
        this.registryService = registryService;
        this.heartbeatExecutor = Executors.newSingleThreadScheduledExecutor(new HeartbeatThreadFactory());
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
        return buildInternal(project, connection, vcsWorkspace, repoSlug, branch,
                revision, kind, includePatterns, excludePatterns, jobId, progressEvents, false);
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
        return buildInternal(project, connection, vcsWorkspace, repoSlug, branch,
                revision, kind, includePatterns, excludePatterns, jobId, progressEvents, true);
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
                revision, kind, includePatterns, excludePatterns, jobId, null, false);
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
            Consumer<Map<String, Object>> progressEvents,
            boolean forceRebuild) throws IOException {
        var registration = registryService.registerBuild(
                project, branch, kind, null, revision,
                forceRebuild ? "operator-refresh:" + requireJobId(jobId) : null);
        if (registration.existingOperation()
                && registration.operation().getStatus()
                == RagIndexOperationStatus.SUCCEEDED) {
            return Map.of(
                    "status", "reused",
                    "collection_target", registration.generation().getCollectionName(),
                    "generation_manifest_sha256", registration.generation().getManifestDigest());
        }

        registryService.startBuild(registration.operation().getId(), jobId);
        ScheduledFuture<?> heartbeat = heartbeatExecutor.scheduleAtFixedRate(
                () -> heartbeat(registration.operation().getId()),
                HEARTBEAT_INTERVAL_SECONDS,
                HEARTBEAT_INTERVAL_SECONDS,
                TimeUnit.SECONDS);
        Path snapshot = null;
        try {
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
            Map<String, Object> result = progressEvents == null
                    ? pipelineClient.indexRepository(
                            snapshot.toString(), project.getWorkspace().getName(),
                            project.getNamespace(), branch, revision, includePatterns,
                            excludePatterns, registration.generation().getCollectionName(),
                            publishBranchAlias, publishLegacyProjectAlias)
                    : pipelineClient.indexRepository(
                            snapshot.toString(), project.getWorkspace().getName(),
                            project.getNamespace(), branch, revision, includePatterns,
                            excludePatterns, registration.generation().getCollectionName(),
                            publishBranchAlias, publishLegacyProjectAlias,
                            progressEvents);
            Object manifest = result.get("generation_manifest_sha256");
            if (!(manifest instanceof String digest) || digest.isBlank()) {
                throw new IOException("RAG full branch generation has no manifest digest");
            }
            registryService.publish(
                    registration.operation().getId(),
                    digest,
                    number(result.get("document_count")),
                    number(result.get("chunk_count")));
            return result;
        } catch (Throwable failure) {
            registryService.fail(
                    registration.operation().getId(),
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
            heartbeat.cancel(false);
            if (snapshot != null) {
                deleteTree(snapshot);
            }
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

    private void heartbeat(long operationId) {
        try {
            registryService.heartbeatBuild(operationId);
        } catch (Exception ignored) {
            // A later heartbeat may still succeed. If the producer actually
            // stops, recovery turns the durable operation into a failure.
        }
    }

    private static final class HeartbeatThreadFactory implements ThreadFactory {
        @Override
        public Thread newThread(Runnable runnable) {
            Thread thread = new Thread(runnable, "rag-generation-heartbeat");
            thread.setDaemon(true);
            return thread;
        }
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

package org.rostilos.codecrow.analysisengine.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.rostilos.codecrow.analysisengine.dto.request.ai.LocalRepositorySnapshot;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.nio.file.InvalidPathException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.Collection;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

/**
 * Prepares an exact target-branch snapshot for repository-aware MCP tools.
 *
 * <p>Snapshot preparation is optional enrichment. Provider or filesystem
 * failures leave the caller on its existing provider-backed MCP path.</p>
 */
@Service
public class LocalRepositorySnapshotService {
    private static final Logger log = LoggerFactory.getLogger(LocalRepositorySnapshotService.class);
    private static final String SNAPSHOT_PREFIX = "codecrow-pr-review-";
    private static final String REVIEW_OVERLAY_PREFIX = "codecrow-pr-overlay-";
    private static final String REVIEW_OVERLAY_MANIFEST = "manifest.json";
    private static final String REVIEW_OVERLAY_FILES = "files";
    private static final Duration ABANDONED_SNAPSHOT_AGE = Duration.ofHours(24);
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    private final BranchArchiveService archiveService;
    private final VcsClientProvider vcsClientProvider;
    private final Path temporaryRoot;

    @Autowired
    public LocalRepositorySnapshotService(
            BranchArchiveService archiveService,
            VcsClientProvider vcsClientProvider
    ) {
        this(archiveService, vcsClientProvider, Path.of(System.getProperty("java.io.tmpdir")));
    }

    LocalRepositorySnapshotService(
            BranchArchiveService archiveService,
            VcsClientProvider vcsClientProvider,
            Path temporaryRoot
    ) {
        this.archiveService = archiveService;
        this.vcsClientProvider = vcsClientProvider;
        this.temporaryRoot = temporaryRoot;
    }

    /** Remove request trees left behind only when their owning process crashed. */
    @Scheduled(
            fixedDelayString = "${codecrow.review.snapshot-cleanup.interval-ms:3600000}",
            initialDelayString = "${codecrow.review.snapshot-cleanup.initial-delay-ms:300000}")
    public void cleanupAbandonedSnapshots() {
        Instant cutoff = Instant.now().minus(ABANDONED_SNAPSHOT_AGE);
        try (var entries = Files.list(temporaryRoot)) {
            entries.filter(Files::isDirectory)
                    .filter(LocalRepositorySnapshotService::isReviewTemporaryTree)
                    .filter(path -> lastModifiedBefore(path, cutoff))
                    .forEach(LocalRepositorySnapshotService::deleteTree);
        } catch (IOException cleanupFailure) {
            log.info("Deferred abandoned review snapshot cleanup under {}: {}",
                    temporaryRoot, cleanupFailure.getMessage());
        }
    }

    private static boolean isReviewTemporaryTree(Path path) {
        String name = path.getFileName() != null
                ? path.getFileName().toString() : "";
        return name.startsWith(SNAPSHOT_PREFIX)
                || name.startsWith(REVIEW_OVERLAY_PREFIX);
    }

    private static boolean lastModifiedBefore(Path path, Instant cutoff) {
        try {
            return Files.getLastModifiedTime(path).toInstant().isBefore(cutoff);
        } catch (IOException unavailable) {
            return false;
        }
    }

    public Optional<PreparedSnapshot> prepare(
            VcsConnection connection,
            String workspace,
            String repoSlug,
            String targetBranch
    ) {
        return prepare(connection, workspace, repoSlug, targetBranch, null);
    }

    public Optional<PreparedSnapshot> prepare(
            VcsConnection connection,
            String workspace,
            String repoSlug,
            String targetBranch,
            String targetHeadRevision
    ) {
        return prepareInternal(
                connection,
                workspace,
                repoSlug,
                targetBranch,
                targetHeadRevision,
                null,
                null,
                null);
    }

    /**
     * Prepares the pinned target tree plus a request-scoped proposed-tree overlay.
     *
     * <p>The overlay contains only PR-modified files. Its manifest also records
     * modified files whose content was unavailable and deleted paths, allowing
     * MCP reads to avoid silently returning stale target-head source.</p>
     */
    public Optional<PreparedSnapshot> prepareForReview(
            VcsConnection connection,
            String workspace,
            String repoSlug,
            String targetBranch,
            String targetHeadRevision,
            Map<String, String> proposedFileContents,
            Collection<String> changedFiles,
            Collection<String> deletedFiles
    ) {
        return prepareInternal(
                connection,
                workspace,
                repoSlug,
                targetBranch,
                targetHeadRevision,
                proposedFileContents,
                changedFiles,
                deletedFiles);
    }

    private Optional<PreparedSnapshot> prepareInternal(
            VcsConnection connection,
            String workspace,
            String repoSlug,
            String targetBranch,
            String targetHeadRevision,
            Map<String, String> proposedFileContents,
            Collection<String> changedFiles,
            Collection<String> deletedFiles
    ) {
        if (targetBranch == null || targetBranch.isBlank()) {
            log.warn("Skipping local MCP repository snapshot because the PR target branch is unavailable");
            return Optional.empty();
        }

        Path snapshotDirectory = null;
        Path reviewOverlayDirectory = null;
        try {
            String targetRevision = targetHeadRevision;
            if (targetRevision == null || targetRevision.isBlank()) {
                VcsClient client = vcsClientProvider.getClient(connection);
                targetRevision = client.getLatestCommitHash(workspace, repoSlug, targetBranch);
            }
            if (targetRevision == null || targetRevision.isBlank()) {
                log.warn(
                        "Skipping local MCP repository snapshot because target head could not be resolved: {}/{} @ {}",
                        workspace, repoSlug, targetBranch);
                return Optional.empty();
            }

            snapshotDirectory = Files.createTempDirectory(temporaryRoot, SNAPSHOT_PREFIX);
            archiveService.downloadAndExtractSnapshotToDirectory(
                    connection,
                    workspace,
                    repoSlug,
                    targetRevision,
                    null,
                    snapshotDirectory);

            if (proposedFileContents != null || changedFiles != null || deletedFiles != null) {
                try {
                    reviewOverlayDirectory = prepareReviewOverlay(
                            proposedFileContents,
                            changedFiles,
                            deletedFiles);
                } catch (Exception overlayFailure) {
                    deleteTree(reviewOverlayDirectory);
                    reviewOverlayDirectory = null;
                    log.warn(
                            "Proposed-tree MCP overlay unavailable for {}/{}; exact target-head tools remain available: {}",
                            workspace,
                            repoSlug,
                            overlayFailure.getMessage());
                }
            }

            log.info(
                    "Prepared local MCP repository snapshot: {}/{} target={} revision={} path={} reviewOverlay={}",
                    workspace,
                    repoSlug,
                    targetBranch,
                    shortRevision(targetRevision),
                    snapshotDirectory,
                    reviewOverlayDirectory);
            return Optional.of(new PreparedSnapshot(
                    snapshotDirectory,
                    targetBranch,
                    targetRevision,
                    reviewOverlayDirectory));
        } catch (Exception failure) {
            log.warn(
                    "Local MCP repository snapshot unavailable for {}/{} target={}; continuing with provider-backed tools: {}",
                    workspace,
                    repoSlug,
                    targetBranch,
                    failure.getMessage());
            deleteTree(snapshotDirectory);
            deleteTree(reviewOverlayDirectory);
            return Optional.empty();
        }
    }

    private Path prepareReviewOverlay(
            Map<String, String> proposedFileContents,
            Collection<String> changedFiles,
            Collection<String> deletedFiles
    ) throws IOException {
        Path overlayRoot = Files.createTempDirectory(temporaryRoot, REVIEW_OVERLAY_PREFIX);
        try {
            Path filesRoot = Files.createDirectory(overlayRoot.resolve(REVIEW_OVERLAY_FILES));

            Set<String> changed = normalizePaths(changedFiles, "changed");
            Set<String> deleted = normalizePaths(deletedFiles, "deleted");
            changed.addAll(deleted);

            Map<String, String> normalizedContents = new LinkedHashMap<>();
            if (proposedFileContents != null) {
                for (Map.Entry<String, String> entry : proposedFileContents.entrySet()) {
                    Optional<String> normalized = normalizeRepositoryPath(entry.getKey());
                    if (normalized.isEmpty()) {
                        log.warn("Skipping unsafe proposed-tree file path: {}", entry.getKey());
                        continue;
                    }
                    changed.add(normalized.get());
                    if (entry.getValue() != null && !deleted.contains(normalized.get())) {
                        normalizedContents.putIfAbsent(normalized.get(), entry.getValue());
                    }
                }
            }

            for (Map.Entry<String, String> entry : normalizedContents.entrySet()) {
                Path destination = filesRoot.resolve(entry.getKey()).normalize();
                if (!destination.startsWith(filesRoot)) {
                    throw new IOException("Proposed-tree path escapes its overlay: " + entry.getKey());
                }
                Path parent = destination.getParent();
                if (parent != null) {
                    Files.createDirectories(parent);
                }
                Files.writeString(destination, entry.getValue());
            }

            ReviewOverlayManifest manifest = new ReviewOverlayManifest(
                    changed.stream().sorted().toList(),
                    deleted.stream().sorted().toList());
            OBJECT_MAPPER.writeValue(
                    overlayRoot.resolve(REVIEW_OVERLAY_MANIFEST).toFile(),
                    manifest);
            return overlayRoot;
        } catch (IOException | RuntimeException failure) {
            deleteTree(overlayRoot);
            throw failure;
        }
    }

    private Set<String> normalizePaths(Collection<String> paths, String kind) {
        Set<String> normalized = new LinkedHashSet<>();
        if (paths == null) {
            return normalized;
        }
        for (String path : paths) {
            Optional<String> safePath = normalizeRepositoryPath(path);
            if (safePath.isPresent()) {
                normalized.add(safePath.get());
            } else {
                log.warn("Skipping unsafe {} proposed-tree path: {}", kind, path);
            }
        }
        return normalized;
    }

    private static Optional<String> normalizeRepositoryPath(String rawPath) {
        if (rawPath == null || rawPath.isBlank()) {
            return Optional.empty();
        }
        try {
            Path normalized = Path.of(rawPath.replace('\\', '/')).normalize();
            String value = normalized.toString().replace('\\', '/');
            if (normalized.isAbsolute()
                    || value.isBlank()
                    || ".".equals(value)
                    || "..".equals(value)
                    || value.startsWith("../")) {
                return Optional.empty();
            }
            return Optional.of(value);
        } catch (InvalidPathException invalidPath) {
            return Optional.empty();
        }
    }

    private record ReviewOverlayManifest(
            List<String> changedFiles,
            List<String> deletedFiles
    ) {
    }

    private static String shortRevision(String revision) {
        return revision.length() > 12 ? revision.substring(0, 12) + "…" : revision;
    }

    private static void deleteTree(Path root) {
        if (root == null) {
            return;
        }
        try {
            if (!Files.exists(root)) {
                return;
            }
            try (var paths = Files.walk(root)) {
                for (Path path : paths.sorted(Comparator.reverseOrder()).toList()) {
                    Files.deleteIfExists(path);
                }
            }
        } catch (IOException cleanupFailure) {
            log.warn("Failed to remove local MCP repository snapshot {}: {}",
                    root, cleanupFailure.getMessage());
        }
    }

    public static final class PreparedSnapshot implements AutoCloseable {
        private final Path path;
        private final String targetBranch;
        private final String revision;
        private final Path reviewOverlayPath;

        private PreparedSnapshot(
                Path path,
                String targetBranch,
                String revision,
                Path reviewOverlayPath
        ) {
            this.path = path;
            this.targetBranch = targetBranch;
            this.revision = revision;
            this.reviewOverlayPath = reviewOverlayPath;
        }

        public Path path() {
            return path;
        }

        public String targetBranch() {
            return targetBranch;
        }

        public String revision() {
            return revision;
        }

        public Path reviewOverlayPath() {
            return reviewOverlayPath;
        }

        public LocalRepositorySnapshot transport() {
            return new LocalRepositorySnapshot(
                    path.toString(),
                    targetBranch,
                    revision,
                    reviewOverlayPath != null ? reviewOverlayPath.toString() : null);
        }

        @Override
        public void close() {
            deleteTree(path);
            deleteTree(reviewOverlayPath);
        }
    }
}

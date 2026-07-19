package org.rostilos.codecrow.pipelineagent.agentic;

import org.rostilos.codecrow.analysisengine.dto.request.ai.AgenticRepositoryArchiveV1;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.DirectoryStream;
import java.nio.file.FileVisitResult;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.attribute.BasicFileAttributes;
import java.nio.file.attribute.FileAttribute;
import java.nio.file.attribute.FileTime;
import java.nio.file.attribute.PosixFilePermission;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.HexFormat;
import java.util.Objects;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Stages one exact-head repository archive for an agentic review.
 *
 * <p>The archive is deliberately kept outside the RAG indexing workspace. The
 * only path handed to the inference worker is derived from a deterministic
 * SHA-256 key, and the actual archive bytes are measured after the VCS client
 * finishes writing them.</p>
 */
@Service
public class AgenticRepositoryArchiveService {

    private static final Logger log = LoggerFactory.getLogger(
            AgenticRepositoryArchiveService.class);

    public static final String STORAGE_ROOT_ENV = "AGENTIC_WORKSPACE_ROOT";
    public static final Path DEFAULT_STORAGE_ROOT = Path.of("/tmp/codecrow-agentic");
    public static final String ARCHIVE_FILE_NAME = "repository.zip";
    public static final Duration STALE_WORKSPACE_AGE = Duration.ofHours(6);
    public static final long MAX_ARCHIVE_BYTES = 512L * 1024L * 1024L;

    private static final int SCHEMA_VERSION = 1;
    private static final Pattern WORKSPACE_KEY = Pattern.compile("[0-9a-f]{64}");
    private static final Pattern EXACT_REVISION =
            Pattern.compile("(?:[0-9a-f]{40}|[0-9a-f]{64})");
    private static final Set<PosixFilePermission> DIRECTORY_PERMISSIONS =
            PosixFilePermissions.fromString("rwx------");
    private static final Set<PosixFilePermission> FILE_PERMISSIONS =
            PosixFilePermissions.fromString("rw-------");
    private static final byte[] WORKSPACE_DOMAIN =
            "codecrow-agentic-repository-v1".getBytes(StandardCharsets.UTF_8);

    private final Path storageRoot;
    private final Clock clock;
    private final long maxArchiveBytes;

    /** Spring/runtime constructor using the shared ephemeral-volume location. */
    public AgenticRepositoryArchiveService() {
        this(
                configuredStorageRoot(System.getenv(STORAGE_ROOT_ENV)),
                Clock.systemUTC(),
                MAX_ARCHIVE_BYTES);
    }

    AgenticRepositoryArchiveService(Path storageRoot, Clock clock) {
        this(storageRoot, clock, MAX_ARCHIVE_BYTES);
    }

    AgenticRepositoryArchiveService(
            Path storageRoot,
            Clock clock,
            long maxArchiveBytes) {
        this.storageRoot = Objects.requireNonNull(storageRoot, "storageRoot")
                .toAbsolutePath()
                .normalize();
        this.clock = Objects.requireNonNull(clock, "clock");
        if (maxArchiveBytes <= 0) {
            throw new IllegalArgumentException("maxArchiveBytes must be positive");
        }
        this.maxArchiveBytes = maxArchiveBytes;
    }

    static Path configuredStorageRoot(String configuredRoot) {
        if (configuredRoot == null || configuredRoot.isBlank()) {
            return DEFAULT_STORAGE_ROOT;
        }
        return Path.of(configuredRoot);
    }

    /**
     * Downloads an archive by immutable head SHA and stages it for inference.
     *
     * @param executionId identity of this review execution; makes concurrent
     *                    reviews of the same repository snapshot independent
     * @param exactHeadSha immutable 40- or 64-hex source revision
     */
    public AgenticRepositoryArchiveV1 stage(
            VcsClient vcsClient,
            String executionId,
            String workspace,
            String repository,
            String exactHeadSha
    ) throws IOException {
        Objects.requireNonNull(vcsClient, "vcsClient");
        requireCoordinate(executionId, "executionId");
        requireCoordinate(workspace, "workspace");
        requireCoordinate(repository, "repository");
        requireExactRevision(exactHeadSha);
        cleanupStaleBestEffort();

        String workspaceKey = workspaceKeyFor(
                executionId, workspace, repository, exactHeadSha);
        ensureSecureStorageRoot();
        Path executionDirectory = resolveWorkspace(workspaceKey);
        boolean directoryCreated = false;

        try {
            createSecureDirectory(executionDirectory);
            directoryCreated = true;
            Path archive = executionDirectory.resolve(ARCHIVE_FILE_NAME);
            createSecureFile(archive);

            // Never substitute a branch name here. The exact revision is the
            // third argument all provider implementations send to the VCS API.
            long downloadedBytes = vcsClient.downloadRepositoryArchiveToFile(
                    workspace,
                    repository,
                    exactHeadSha,
                    archive,
                    maxArchiveBytes);

            requireRegularArchive(archive);
            setOwnerOnlyPermissions(archive, false);
            long byteLength = Files.size(archive);
            if (byteLength <= 0) {
                throw new IOException("Downloaded agentic repository archive is empty");
            }
            if (byteLength > maxArchiveBytes || downloadedBytes > maxArchiveBytes) {
                throw new IOException(
                        "Downloaded agentic repository archive exceeds size limit");
            }
            String contentDigest = sha256(archive);
            Files.setLastModifiedTime(
                    executionDirectory, FileTime.from(clock.instant()));

            return new AgenticRepositoryArchiveV1(
                    SCHEMA_VERSION,
                    workspaceKey,
                    exactHeadSha,
                    contentDigest,
                    byteLength);
        } catch (IOException | RuntimeException | Error failure) {
            if (directoryCreated) {
                try {
                    deletePath(executionDirectory);
                } catch (IOException cleanupFailure) {
                    failure.addSuppressed(cleanupFailure);
                }
            }
            throw failure;
        }
    }

    /**
     * Deletes a staged execution immediately. Repeated calls are safe.
     *
     * @return {@code true} when a workspace existed and was removed
     */
    public boolean cleanup(String workspaceKey) throws IOException {
        requireWorkspaceKey(workspaceKey);
        if (!Files.exists(storageRoot, LinkOption.NOFOLLOW_LINKS)) {
            return false;
        }
        requireSecureStorageRoot();
        Path workspace = resolveWorkspace(workspaceKey);
        if (!Files.exists(workspace, LinkOption.NOFOLLOW_LINKS)) {
            return false;
        }
        try {
            deletePath(workspace);
            return true;
        } catch (NoSuchFileException racedCleanup) {
            return false;
        }
    }

    /**
     * Removes crash leftovers older than {@code maximumAge}. Only canonical
     * 64-hex workspace entries are considered; unrelated files are untouched.
     */
    public int cleanupStale(Duration maximumAge) throws IOException {
        Objects.requireNonNull(maximumAge, "maximumAge");
        if (maximumAge.isNegative()) {
            throw new IllegalArgumentException("maximumAge cannot be negative");
        }
        if (!Files.exists(storageRoot, LinkOption.NOFOLLOW_LINKS)) {
            return 0;
        }
        requireSecureStorageRoot();
        Instant cutoff = clock.instant().minus(maximumAge);
        int removed = 0;

        try (DirectoryStream<Path> entries = Files.newDirectoryStream(storageRoot)) {
            for (Path candidate : entries) {
                String name = candidate.getFileName().toString();
                if (!WORKSPACE_KEY.matcher(name).matches()) {
                    continue;
                }
                FileTime modified;
                try {
                    modified = Files.getLastModifiedTime(
                            candidate, LinkOption.NOFOLLOW_LINKS);
                } catch (NoSuchFileException racedCleanup) {
                    continue;
                }
                if (modified.toInstant().isAfter(cutoff)) {
                    continue;
                }
                try {
                    deletePath(resolveWorkspace(name));
                    removed++;
                } catch (NoSuchFileException racedCleanup) {
                    // Another cleanup owner already completed this workspace.
                }
            }
        }
        return removed;
    }

    private void cleanupStaleBestEffort() {
        try {
            cleanupStale(STALE_WORKSPACE_AGE);
        } catch (IOException cleanupFailure) {
            log.warn(
                    "Agentic repository stale-workspace cleanup failed before staging: {}",
                    cleanupFailure.getClass().getSimpleName());
        }
    }

    /**
     * Stable, unambiguous key for an execution-owned repository snapshot.
     */
    public static String workspaceKeyFor(
            String executionId,
            String workspace,
            String repository,
            String exactHeadSha
    ) {
        requireCoordinate(executionId, "executionId");
        requireCoordinate(workspace, "workspace");
        requireCoordinate(repository, "repository");
        requireExactRevision(exactHeadSha);

        MessageDigest digest = newSha256();
        digest.update(WORKSPACE_DOMAIN);
        updateLengthPrefixed(digest, executionId);
        updateLengthPrefixed(digest, workspace);
        updateLengthPrefixed(digest, repository);
        updateLengthPrefixed(digest, exactHeadSha);
        return HexFormat.of().formatHex(digest.digest());
    }

    private void ensureSecureStorageRoot() throws IOException {
        if (!Files.exists(storageRoot, LinkOption.NOFOLLOW_LINKS)) {
            Path parent = storageRoot.getParent();
            if (parent != null && Files.exists(parent)
                    && supportsPosix(parent)) {
                FileAttribute<Set<PosixFilePermission>> attribute =
                        PosixFilePermissions.asFileAttribute(DIRECTORY_PERMISSIONS);
                Files.createDirectories(storageRoot, attribute);
            } else {
                Files.createDirectories(storageRoot);
            }
        }
        requireSecureStorageRoot();
        setOwnerOnlyPermissions(storageRoot, true);
    }

    private void requireSecureStorageRoot() throws IOException {
        if (Files.isSymbolicLink(storageRoot)
                || !Files.isDirectory(storageRoot, LinkOption.NOFOLLOW_LINKS)) {
            throw new IOException("Agentic repository storage root is not a secure directory");
        }
    }

    private Path resolveWorkspace(String workspaceKey) throws IOException {
        requireWorkspaceKey(workspaceKey);
        Path resolved = storageRoot.resolve(workspaceKey).normalize();
        if (!resolved.getParent().equals(storageRoot)) {
            throw new IOException("Agentic workspace escapes storage root");
        }
        return resolved;
    }

    private static void createSecureDirectory(Path path) throws IOException {
        if (supportsPosix(path.getParent())) {
            FileAttribute<Set<PosixFilePermission>> attribute =
                    PosixFilePermissions.asFileAttribute(DIRECTORY_PERMISSIONS);
            Files.createDirectory(path, attribute);
        } else {
            Files.createDirectory(path);
            setOwnerOnlyPermissions(path, true);
        }
    }

    private static void createSecureFile(Path path) throws IOException {
        if (supportsPosix(path.getParent())) {
            FileAttribute<Set<PosixFilePermission>> attribute =
                    PosixFilePermissions.asFileAttribute(FILE_PERMISSIONS);
            Files.createFile(path, attribute);
        } else {
            Files.createFile(path);
            setOwnerOnlyPermissions(path, false);
        }
    }

    private static boolean supportsPosix(Path existingPath) throws IOException {
        return Files.getFileStore(existingPath).supportsFileAttributeView("posix");
    }

    private static void setOwnerOnlyPermissions(Path path, boolean directory)
            throws IOException {
        if (supportsPosix(path)) {
            Files.setPosixFilePermissions(
                    path, directory ? DIRECTORY_PERMISSIONS : FILE_PERMISSIONS);
            return;
        }

        java.io.File file = path.toFile();
        boolean secured = file.setReadable(false, false)
                && file.setWritable(false, false)
                && file.setExecutable(false, false)
                && file.setReadable(true, true)
                && file.setWritable(true, true);
        if (directory) {
            secured = file.setExecutable(true, true) && secured;
        }
        if (!secured) {
            throw new IOException("Cannot set owner-only permissions for " + path);
        }
    }

    private static void requireRegularArchive(Path archive) throws IOException {
        if (Files.isSymbolicLink(archive)
                || !Files.isRegularFile(archive, LinkOption.NOFOLLOW_LINKS)) {
            throw new IOException("Downloaded agentic repository archive is not a regular file");
        }
    }

    private static String sha256(Path path) throws IOException {
        MessageDigest digest = newSha256();
        byte[] buffer = new byte[64 * 1024];
        try (InputStream input = Files.newInputStream(path)) {
            int read;
            while ((read = input.read(buffer)) != -1) {
                digest.update(buffer, 0, read);
            }
        }
        return HexFormat.of().formatHex(digest.digest());
    }

    private static MessageDigest newSha256() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("JVM does not provide SHA-256", impossible);
        }
    }

    private static void updateLengthPrefixed(MessageDigest digest, String value) {
        byte[] encoded = value.getBytes(StandardCharsets.UTF_8);
        digest.update(ByteBuffer.allocate(Integer.BYTES).putInt(encoded.length).array());
        digest.update(encoded);
    }

    private static void requireCoordinate(String value, String name) {
        Objects.requireNonNull(value, name);
        if (value.isBlank()) {
            throw new IllegalArgumentException(name + " cannot be blank");
        }
    }

    private static void requireExactRevision(String exactHeadSha) {
        Objects.requireNonNull(exactHeadSha, "exactHeadSha");
        if (!EXACT_REVISION.matcher(exactHeadSha).matches()) {
            throw new IllegalArgumentException(
                    "exactHeadSha must be an immutable 40- or 64-hex revision");
        }
    }

    private static void requireWorkspaceKey(String workspaceKey) {
        Objects.requireNonNull(workspaceKey, "workspaceKey");
        if (!WORKSPACE_KEY.matcher(workspaceKey).matches()) {
            throw new IllegalArgumentException("workspaceKey must be 64 lowercase hex characters");
        }
    }

    private static void deletePath(Path root) throws IOException {
        if (Files.isSymbolicLink(root)) {
            Files.delete(root);
            return;
        }
        Files.walkFileTree(root, new SimpleFileVisitor<>() {
            @Override
            public FileVisitResult visitFile(Path file, BasicFileAttributes attributes)
                    throws IOException {
                Files.delete(file);
                return FileVisitResult.CONTINUE;
            }

            @Override
            public FileVisitResult postVisitDirectory(Path directory, IOException failure)
                    throws IOException {
                if (failure != null) {
                    throw failure;
                }
                Files.delete(directory);
                return FileVisitResult.CONTINUE;
            }
        });
    }
}

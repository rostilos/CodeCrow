package org.rostilos.codecrow.pipelineagent.agentic;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AgenticRepositoryArchiveV1;
import org.rostilos.codecrow.vcsclient.VcsClient;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.lang.reflect.Proxy;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.HexFormat;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class AgenticRepositoryArchiveServiceTest {

    private static final String HEAD_SHA = "a".repeat(40);
    private static final Instant NOW = Instant.parse("2026-07-17T12:00:00Z");

    @Test
    void runtimeStorageRootUsesTheSharedEnvironmentSetting() {
        assertThat(AgenticRepositoryArchiveService.configuredStorageRoot(
                "/shared/codecrow-agentic"))
                .isEqualTo(Path.of("/shared/codecrow-agentic"));
        assertThat(AgenticRepositoryArchiveService.configuredStorageRoot(null))
                .isEqualTo(AgenticRepositoryArchiveService.DEFAULT_STORAGE_ROOT);
        assertThat(AgenticRepositoryArchiveService.configuredStorageRoot("  "))
                .isEqualTo(AgenticRepositoryArchiveService.DEFAULT_STORAGE_ROOT);
    }

    @Test
    void stageDownloadsOnlyTheExactHeadAndDescribesTheObservedArchive(
            @TempDir Path tempDir) throws Exception {
        byte[] archive = "exact repository archive".getBytes(StandardCharsets.UTF_8);
        AgenticRepositoryArchiveService service = service(tempDir);
        String executionId = "execution-42";
        String expectedKey = AgenticRepositoryArchiveService.workspaceKeyFor(
                executionId, "workspace", "repository", HEAD_SHA);

        AtomicInteger downloads = new AtomicInteger();
        VcsClient vcsClient = vcsClient((workspace, repository, revision, target) -> {
                    downloads.incrementAndGet();
                    assertThat(workspace).isEqualTo("workspace");
                    assertThat(repository).isEqualTo("repository");
                    assertThat(revision).isEqualTo(HEAD_SHA);
                    assertThat(target).isEqualTo(
                            tempDir.resolve(expectedKey).resolve("repository.zip"));
                    Files.write(target, archive);
                    // The service must describe the file rather than trusting this value.
                    return 1L;
                });

        AgenticRepositoryArchiveV1 descriptor = service.stage(
                vcsClient, executionId, "workspace", "repository", HEAD_SHA);

        assertThat(descriptor.schemaVersion()).isEqualTo(1);
        assertThat(descriptor.workspaceKey()).isEqualTo(expectedKey)
                .matches("[0-9a-f]{64}");
        assertThat(descriptor.snapshotSha()).isEqualTo(HEAD_SHA);
        assertThat(descriptor.contentDigest()).isEqualTo(sha256(archive));
        assertThat(descriptor.byteLength()).isEqualTo(archive.length);
        assertThat(Files.readAllBytes(
                tempDir.resolve(expectedKey).resolve("repository.zip")))
                .isEqualTo(archive);

        if (Files.getFileStore(tempDir).supportsFileAttributeView("posix")) {
            assertThat(Files.getPosixFilePermissions(
                    tempDir, LinkOption.NOFOLLOW_LINKS))
                    .isEqualTo(PosixFilePermissions.fromString("rwx------"));
            assertThat(Files.getPosixFilePermissions(
                    tempDir.resolve(expectedKey), LinkOption.NOFOLLOW_LINKS))
                    .isEqualTo(PosixFilePermissions.fromString("rwx------"));
            assertThat(Files.getPosixFilePermissions(
                    tempDir.resolve(expectedKey).resolve("repository.zip"),
                    LinkOption.NOFOLLOW_LINKS))
                    .isEqualTo(PosixFilePermissions.fromString("rw-------"));
        }

        assertThat(downloads).hasValue(1);
    }

    @Test
    void workspaceKeyIsDeterministicAndSeparatesConcurrentExecutions() {
        String first = AgenticRepositoryArchiveService.workspaceKeyFor(
                "execution-a", "workspace", "repository", HEAD_SHA);
        String repeated = AgenticRepositoryArchiveService.workspaceKeyFor(
                "execution-a", "workspace", "repository", HEAD_SHA);
        String concurrent = AgenticRepositoryArchiveService.workspaceKeyFor(
                "execution-b", "workspace", "repository", HEAD_SHA);

        assertThat(first).isEqualTo(repeated).matches("[0-9a-f]{64}");
        assertThat(concurrent).matches("[0-9a-f]{64}").isNotEqualTo(first);
    }

    @Test
    void downloadFailureRemovesThePartialExecutionDirectory(@TempDir Path tempDir)
            throws Exception {
        AgenticRepositoryArchiveService service = service(tempDir);
        String key = AgenticRepositoryArchiveService.workspaceKeyFor(
                "execution-failed", "workspace", "repository", HEAD_SHA);

        VcsClient vcsClient = vcsClient((workspace, repository, revision, target) -> {
                    assertThat(revision).isEqualTo(HEAD_SHA);
                    Files.writeString(target, "partial");
                    throw new IOException("download interrupted");
                });

        assertThatThrownBy(() -> service.stage(
                vcsClient, "execution-failed", "workspace", "repository", HEAD_SHA))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("download interrupted");

        assertThat(tempDir.resolve(key)).doesNotExist();
    }

    @Test
    void emptyArchiveIsRejectedAndCleaned(@TempDir Path tempDir) throws Exception {
        AgenticRepositoryArchiveService service = service(tempDir);
        String key = AgenticRepositoryArchiveService.workspaceKeyFor(
                "execution-empty", "workspace", "repository", HEAD_SHA);
        VcsClient vcsClient = vcsClient(
                (workspace, repository, revision, target) -> 0L);

        assertThatThrownBy(() -> service.stage(
                vcsClient, "execution-empty", "workspace", "repository", HEAD_SHA))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("empty");

        assertThat(tempDir.resolve(key)).doesNotExist();
    }

    @Test
    void oversizedArchiveIsRejectedAndCleaned(@TempDir Path tempDir) throws Exception {
        AgenticRepositoryArchiveService service =
                new AgenticRepositoryArchiveService(
                        tempDir, Clock.fixed(NOW, ZoneOffset.UTC), 8);
        String key = AgenticRepositoryArchiveService.workspaceKeyFor(
                "execution-oversized", "workspace", "repository", HEAD_SHA);
        VcsClient vcsClient = vcsClient((workspace, repository, revision, target) -> {
            Files.writeString(target, "larger than eight bytes");
            return Files.size(target);
        });

        assertThatThrownBy(() -> service.stage(
                vcsClient, "execution-oversized", "workspace", "repository", HEAD_SHA))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("size limit");

        assertThat(tempDir.resolve(key)).doesNotExist();
    }

    @Test
    void invalidRevisionCannotBeDowngradedToABranchDownload(@TempDir Path tempDir) {
        AgenticRepositoryArchiveService service = service(tempDir);
        AtomicInteger downloads = new AtomicInteger();
        VcsClient vcsClient = vcsClient((workspace, repository, revision, target) -> {
            downloads.incrementAndGet();
            return 0L;
        });

        assertThatThrownBy(() -> service.stage(
                vcsClient, "execution", "workspace", "repository", "main"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("exactHeadSha");

        assertThat(downloads).hasValue(0);
    }

    @Test
    void cleanupStaleRemovesOnlyExpiredWorkspaceKeys(@TempDir Path tempDir)
            throws Exception {
        AgenticRepositoryArchiveService service = service(tempDir);
        String expiredKey = "1".repeat(64);
        String freshKey = "2".repeat(64);
        Path expired = Files.createDirectory(tempDir.resolve(expiredKey));
        Files.writeString(expired.resolve("repository.zip"), "expired");
        Path fresh = Files.createDirectory(tempDir.resolve(freshKey));
        Files.writeString(fresh.resolve("repository.zip"), "fresh");
        Path unrelated = Files.createDirectory(tempDir.resolve("keep-me"));
        Files.setLastModifiedTime(expired, FileTime.from(NOW.minus(Duration.ofHours(3))));
        Files.setLastModifiedTime(fresh, FileTime.from(NOW.minus(Duration.ofMinutes(30))));
        Files.setLastModifiedTime(unrelated, FileTime.from(NOW.minus(Duration.ofDays(2))));

        int removed = service.cleanupStale(Duration.ofHours(1));

        assertThat(removed).isEqualTo(1);
        assertThat(expired).doesNotExist();
        assertThat(fresh).exists();
        assertThat(unrelated).exists();
    }

    @Test
    void stageSweepsExpiredWorkspacesWithoutTouchingFreshCanonicalWork(
            @TempDir Path tempDir) throws Exception {
        AgenticRepositoryArchiveService service = service(tempDir);
        Path expired = Files.createDirectory(tempDir.resolve("4".repeat(64)));
        Files.writeString(expired.resolve("repository.zip"), "expired");
        Path fresh = Files.createDirectory(tempDir.resolve("5".repeat(64)));
        Files.writeString(fresh.resolve("repository.zip"), "active");
        Files.setLastModifiedTime(
                expired,
                FileTime.from(NOW.minus(AgenticRepositoryArchiveService
                        .STALE_WORKSPACE_AGE).minusSeconds(1)));
        Files.setLastModifiedTime(
                fresh,
                FileTime.from(NOW.minus(AgenticRepositoryArchiveService
                        .STALE_WORKSPACE_AGE).plusSeconds(1)));

        AgenticRepositoryArchiveV1 staged = service.stage(
                vcsClient((workspace, repository, revision, target) -> {
                    Files.writeString(target, "new archive");
                    return Files.size(target);
                }),
                "execution-cleanup",
                "workspace",
                "repository",
                HEAD_SHA);

        assertThat(expired).doesNotExist();
        assertThat(fresh).exists();
        assertThat(tempDir.resolve(staged.workspaceKey())).exists();
    }

    @Test
    void cleanupFailureDoesNotReuseOrOverwriteAnExistingWorkspace(
            @TempDir Path tempDir) throws Exception {
        String existingKey = "6".repeat(64);
        Path existing = Files.createDirectory(tempDir.resolve(existingKey));
        Path existingArchive = Files.writeString(
                existing.resolve("repository.zip"), "active archive");
        AgenticRepositoryArchiveService service =
                new AgenticRepositoryArchiveService(
                        tempDir, Clock.fixed(NOW, ZoneOffset.UTC)) {
                    @Override
                    public int cleanupStale(Duration maximumAge) throws IOException {
                        throw new IOException("simulated cleanup failure");
                    }
                };

        AgenticRepositoryArchiveV1 staged = service.stage(
                vcsClient((workspace, repository, revision, target) -> {
                    Files.writeString(target, "new archive");
                    return Files.size(target);
                }),
                "execution-after-cleanup-failure",
                "workspace",
                "repository",
                HEAD_SHA);

        assertThat(staged.workspaceKey()).isNotEqualTo(existingKey);
        assertThat(Files.readString(existingArchive)).isEqualTo("active archive");
        assertThat(tempDir.resolve(staged.workspaceKey())).exists();
    }

    @Test
    void immediateCleanupIsIdempotent(@TempDir Path tempDir) throws Exception {
        AgenticRepositoryArchiveService service = service(tempDir);
        String key = "3".repeat(64);
        Path workspace = Files.createDirectory(tempDir.resolve(key));
        Files.writeString(workspace.resolve("repository.zip"), "archive");

        assertThat(service.cleanup(key)).isTrue();
        assertThat(service.cleanup(key)).isFalse();
        assertThat(workspace).doesNotExist();
    }

    private static AgenticRepositoryArchiveService service(Path root) {
        return new AgenticRepositoryArchiveService(
                root, Clock.fixed(NOW, ZoneOffset.UTC));
    }

    private static String sha256(byte[] value) throws Exception {
        return HexFormat.of().formatHex(
                MessageDigest.getInstance("SHA-256").digest(value));
    }

    private static VcsClient vcsClient(ArchiveDownload archiveDownload) {
        return (VcsClient) Proxy.newProxyInstance(
                VcsClient.class.getClassLoader(),
                new Class<?>[]{VcsClient.class},
                (proxy, method, arguments) -> {
                    if (method.getName().equals("downloadRepositoryArchiveToFile")) {
                        return archiveDownload.download(
                                (String) arguments[0],
                                (String) arguments[1],
                                (String) arguments[2],
                                (Path) arguments[3]);
                    }
                    if (method.getDeclaringClass() == Object.class) {
                        return switch (method.getName()) {
                            case "toString" -> "AgenticArchiveTestVcsClient";
                            case "hashCode" -> System.identityHashCode(proxy);
                            case "equals" -> proxy == arguments[0];
                            default -> throw new AssertionError(
                                    "Unexpected Object method: " + method.getName());
                        };
                    }
                    throw new AssertionError("Unexpected VCS call: " + method.getName());
                });
    }

    @FunctionalInterface
    private interface ArchiveDownload {
        long download(
                String workspace,
                String repository,
                String revision,
                Path target
        ) throws IOException;
    }
}

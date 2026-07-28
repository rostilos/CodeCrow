package org.rostilos.codecrow.vcsclient.bitbucket.cloud;

import java.io.IOException;
import java.io.OutputStream;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.FileVisitResult;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.StandardCopyOption;
import java.nio.file.attribute.BasicFileAttributes;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

/**
 * Acquires a private Bitbucket Cloud repository through Git's authenticated
 * HTTPS transport and writes a deterministic working-tree ZIP.
 *
 * <p>Bitbucket's browser archive URL does not accept OAuth Bearer headers for
 * private repositories. Git HTTPS does accept an OAuth access token using the
 * {@code x-token-auth} username. The authorization header is supplied through
 * child-process environment configuration, never through the repository URL or
 * command arguments.</p>
 */
final class BitbucketGitArchiveDownloader {
    private static final Duration DEFAULT_TIMEOUT = Duration.ofMinutes(5);
    private static final String GIT_HOST = "https://bitbucket.org";
    private static final String ARCHIVE_ROOT = "codecrow-repository-snapshot-000000000000/";

    @FunctionalInterface
    interface GitCommandRunner {
        void run(Path workingDirectory, Map<String, String> environment, List<String> arguments)
                throws IOException;
    }

    private final String accessToken;
    private final GitCommandRunner commandRunner;

    BitbucketGitArchiveDownloader(String accessToken) {
        this(accessToken, new SystemGitCommandRunner("git", DEFAULT_TIMEOUT));
    }

    BitbucketGitArchiveDownloader(String accessToken, GitCommandRunner commandRunner) {
        if (accessToken == null || accessToken.isBlank()) {
            throw new IllegalArgumentException("Bitbucket Git access token cannot be null or blank");
        }
        this.accessToken = accessToken;
        this.commandRunner = commandRunner;
    }

    long download(String workspace, String repository, String ref, Path targetFile) throws IOException {
        validateIdentifier("workspace", workspace);
        validateIdentifier("repository", repository);
        validateRef(ref);

        Path absoluteTarget = targetFile.toAbsolutePath().normalize();
        Path targetDirectory = absoluteTarget.getParent();
        if (targetDirectory == null) {
            throw new IOException("Repository archive target must have a parent directory");
        }
        Files.createDirectories(targetDirectory);

        Path checkout = Files.createTempDirectory("codecrow-bitbucket-snapshot-");
        Path partialArchive = Files.createTempFile(
                targetDirectory, absoluteTarget.getFileName().toString() + ".", ".partial");

        try {
            Map<String, String> environment = gitEnvironment();
            commandRunner.run(checkout, environment, List.of("init", "--quiet"));
            commandRunner.run(checkout, environment, List.of(
                    "remote", "add", "origin", repositoryUrl(workspace, repository)));
            commandRunner.run(checkout, environment, List.of(
                    "fetch", "--quiet", "--no-tags", "--depth=1", "origin", ref));
            commandRunner.run(checkout, environment, List.of(
                    "checkout", "--quiet", "--detach", "FETCH_HEAD"));

            writeWorkingTreeArchive(checkout, partialArchive);
            moveCompletedArchive(partialArchive, absoluteTarget);
            return Files.size(absoluteTarget);
        } catch (IOException | RuntimeException exception) {
            Files.deleteIfExists(partialArchive);
            throw exception;
        } finally {
            deleteRecursively(checkout);
        }
    }

    private Map<String, String> gitEnvironment() {
        String basicCredential = Base64.getEncoder().encodeToString(
                ("x-token-auth:" + accessToken).getBytes(StandardCharsets.UTF_8));

        Map<String, String> environment = new LinkedHashMap<>();
        environment.put("GIT_TERMINAL_PROMPT", "0");
        environment.put("GIT_CONFIG_COUNT", "1");
        environment.put("GIT_CONFIG_KEY_0", "http.https://bitbucket.org/.extraHeader");
        environment.put("GIT_CONFIG_VALUE_0", "Authorization: Basic " + basicCredential);
        environment.put("GIT_LFS_SKIP_SMUDGE", "1");
        return environment;
    }

    private static String repositoryUrl(String workspace, String repository) {
        return GIT_HOST + "/" + encodePathSegment(workspace) + "/"
                + encodePathSegment(repository) + ".git";
    }

    private static String encodePathSegment(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8).replace("+", "%20");
    }

    private static void validateIdentifier(String name, String value) {
        if (value == null || value.isBlank() || containsControlCharacter(value)) {
            throw new IllegalArgumentException("Bitbucket " + name + " cannot be null, blank, or contain controls");
        }
    }

    private static void validateRef(String ref) {
        if (ref == null || ref.isBlank() || ref.startsWith("-") || containsControlCharacter(ref)) {
            throw new IllegalArgumentException(
                    "Bitbucket branch or commit cannot be null, blank, option-like, or contain controls");
        }
    }

    private static boolean containsControlCharacter(String value) {
        return value.chars().anyMatch(Character::isISOControl);
    }

    private static void writeWorkingTreeArchive(Path checkout, Path target) throws IOException {
        List<Path> paths = new ArrayList<>();
        try (var stream = Files.walk(checkout)) {
            stream.filter(path -> !path.equals(checkout))
                    .filter(path -> !isGitMetadata(checkout, path))
                    .filter(path -> !Files.isSymbolicLink(path))
                    .sorted(Comparator.comparing(path -> archiveName(checkout, path)))
                    .forEach(paths::add);
        }

        try (OutputStream output = Files.newOutputStream(target);
             ZipOutputStream zip = new ZipOutputStream(output, StandardCharsets.UTF_8)) {
            ZipEntry rootEntry = new ZipEntry(ARCHIVE_ROOT);
            rootEntry.setTime(0L);
            zip.putNextEntry(rootEntry);
            zip.closeEntry();
            for (Path path : paths) {
                String name = ARCHIVE_ROOT + archiveName(checkout, path);
                boolean directory = Files.isDirectory(path);
                ZipEntry entry = new ZipEntry(directory ? name + "/" : name);
                entry.setTime(0L);
                zip.putNextEntry(entry);
                if (!directory) {
                    Files.copy(path, zip);
                }
                zip.closeEntry();
            }
        }
    }

    private static boolean isGitMetadata(Path checkout, Path path) {
        Path relative = checkout.relativize(path);
        return relative.getNameCount() > 0 && ".git".equals(relative.getName(0).toString());
    }

    private static String archiveName(Path checkout, Path path) {
        return checkout.relativize(path).toString().replace('\\', '/');
    }

    private static void moveCompletedArchive(Path source, Path target) throws IOException {
        try {
            Files.move(source, target,
                    StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } catch (AtomicMoveNotSupportedException ignored) {
            Files.move(source, target, StandardCopyOption.REPLACE_EXISTING);
        }
    }

    private static void deleteRecursively(Path root) {
        if (root == null || !Files.exists(root)) {
            return;
        }
        try {
            Files.walkFileTree(root, new SimpleFileVisitor<>() {
                @Override
                public FileVisitResult visitFile(Path file, BasicFileAttributes attributes)
                        throws IOException {
                    Files.deleteIfExists(file);
                    return FileVisitResult.CONTINUE;
                }

                @Override
                public FileVisitResult postVisitDirectory(Path directory, IOException exception)
                        throws IOException {
                    if (exception != null) {
                        throw exception;
                    }
                    Files.deleteIfExists(directory);
                    return FileVisitResult.CONTINUE;
                }
            });
        } catch (IOException ignored) {
            // Best-effort cleanup must not mask the repository acquisition result.
        }
    }

    private static final class SystemGitCommandRunner implements GitCommandRunner {
        private final String executable;
        private final Duration timeout;

        private SystemGitCommandRunner(String executable, Duration timeout) {
            this.executable = executable;
            this.timeout = timeout;
        }

        @Override
        public void run(
                Path workingDirectory,
                Map<String, String> environment,
                List<String> arguments
        ) throws IOException {
            List<String> command = new ArrayList<>(arguments.size() + 1);
            command.add(executable);
            command.addAll(arguments);

            ProcessBuilder processBuilder = new ProcessBuilder(command)
                    .directory(workingDirectory.toFile())
                    .redirectOutput(ProcessBuilder.Redirect.DISCARD)
                    .redirectError(ProcessBuilder.Redirect.DISCARD);
            processBuilder.environment().putAll(environment);

            Process process;
            try {
                process = processBuilder.start();
            } catch (IOException exception) {
                throw new IOException(
                        "Git executable is unavailable for Bitbucket repository acquisition", exception);
            }

            try {
                if (!process.waitFor(timeout.toMillis(), TimeUnit.MILLISECONDS)) {
                    process.destroyForcibly();
                    throw new IOException("Timed out acquiring Bitbucket repository snapshot");
                }
                if (process.exitValue() != 0) {
                    throw new IOException(
                            "Git command failed while acquiring Bitbucket repository snapshot"
                                    + " (exit " + process.exitValue() + ")");
                }
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
                process.destroyForcibly();
                throw new IOException("Interrupted while acquiring Bitbucket repository snapshot", exception);
            }
        }
    }
}

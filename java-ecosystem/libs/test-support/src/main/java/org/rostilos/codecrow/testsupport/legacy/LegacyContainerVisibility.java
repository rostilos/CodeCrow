package org.rostilos.codecrow.testsupport.legacy;

import java.io.BufferedReader;
import java.io.IOException;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Active-lane proof that the JVM cannot see a container control surface. */
final class LegacyContainerVisibility {

    private static final int MAX_MOUNT_INFO_CHARS = 1_048_576;
    private static final int MAX_UNIX_SOCKET_CHARS = 1_048_576;
    private static final int MAX_FILE_DESCRIPTORS = 4096;
    private static final Pattern SOCKET_FD = Pattern.compile("^socket:\\[([0-9]+)]$");
    private static final List<Path> CONTROL_PATHS = List.of(
            Path.of("/run/docker.sock"),
            Path.of("/var/run/docker.sock"),
            Path.of("/run/containerd/containerd.sock"),
            Path.of("/var/run/containerd/containerd.sock"),
            Path.of("/run/podman/podman.sock"),
            Path.of("/var/run/podman/podman.sock"),
            Path.of("/run/codecrow-host-proxy"),
            Path.of("/var/run/codecrow-host-proxy")
    );
    private static final List<String> SOCKET_MARKERS = List.of(
            "docker.sock",
            "containerd.sock",
            "podman.sock"
    );

    private LegacyContainerVisibility() {
    }

    static Snapshot capture() {
        List<String> paths = new ArrayList<>();
        for (Path controlPath : CONTROL_PATHS) {
            if (Files.exists(controlPath, LinkOption.NOFOLLOW_LINKS)) {
                paths.add(controlPath.toString());
            }
        }
        String mountInfo = readBounded(
                Path.of("/proc/self/mountinfo"),
                MAX_MOUNT_INFO_CHARS,
                "mount information"
        );
        String unixSocketsBefore = readBounded(
                Path.of("/proc/self/net/unix"),
                MAX_UNIX_SOCKET_CHARS,
                "Unix socket table"
        );
        List<String> descriptors = readFileDescriptors();
        String unixSocketsAfter = readBounded(
                Path.of("/proc/self/net/unix"),
                MAX_UNIX_SOCKET_CHARS,
                "Unix socket table"
        );
        return new Snapshot(
                paths,
                mountInfo,
                descriptors,
                unixSocketsBefore + "\n" + unixSocketsAfter
        );
    }

    static void assertHidden(Map<String, String> environment, Snapshot snapshot) {
        environment.keySet().stream()
                .filter(key -> key.startsWith("DOCKER_") || key.startsWith("TESTCONTAINERS_"))
                .sorted()
                .findFirst()
                .ifPresent(key -> {
                    throw new IllegalStateException(
                            "guarded JVM exposes forbidden runtime variable " + key
                    );
                });

        for (String path : snapshot.visiblePaths()) {
            if (containsSocketMarker(path)) {
                throw new IllegalStateException(
                        "guarded JVM exposes a container control socket: " + path
                );
            }
            if (isHostProxy(path)) {
                throw new IllegalStateException("guarded JVM exposes a host proxy mount");
            }
        }
        if (containsSocketMarker(snapshot.mountInfo())) {
            throw new IllegalStateException(
                    "guarded JVM mount table exposes a container control socket"
            );
        }
        if (isHostProxy(snapshot.mountInfo())) {
            throw new IllegalStateException("guarded JVM exposes a host proxy mount");
        }
        Set<String> unixSocketInodes = unixSocketInodes(snapshot.unixSocketTable());
        Set<String> namedUnixSocketInodes = namedUnixSocketInodes(
                snapshot.unixSocketTable()
        );
        Set<String> anonymousRuntimeSocketInodes = new HashSet<>();
        for (String descriptor : snapshot.fileDescriptorTargets()) {
            if (containsSocketMarker(descriptor) || isHostProxy(descriptor)) {
                throw new IllegalStateException(
                        "guarded JVM file descriptor exposes a container control surface"
                );
            }
            Matcher socket = SOCKET_FD.matcher(descriptor);
            if (socket.matches() && unixSocketInodes.contains(socket.group(1))) {
                String inode = socket.group(1);
                if (namedUnixSocketInodes.contains(inode)) {
                    throw new IllegalStateException(
                            "guarded JVM exposes a named Unix socket file descriptor"
                    );
                }
                anonymousRuntimeSocketInodes.add(inode);
            }
        }
        if (anonymousRuntimeSocketInodes.size() > 1) {
            throw new IllegalStateException(
                    "guarded JVM exposes multiple anonymous Unix socket file descriptors"
            );
        }
    }

    private static String readBounded(Path path, int maximumCharacters, String description) {
        StringBuilder content = new StringBuilder();
        try (BufferedReader reader = Files.newBufferedReader(path)) {
            char[] buffer = new char[8192];
            int read;
            while ((read = reader.read(buffer)) != -1) {
                if (content.length() + read > maximumCharacters) {
                    throw new IllegalStateException(description + " exceeds the safety bound");
                }
                content.append(buffer, 0, read);
            }
            return content.toString();
        } catch (IOException failure) {
            throw new IllegalStateException("cannot inspect guarded JVM " + description);
        }
    }

    private static List<String> readFileDescriptors() {
        List<String> targets = new ArrayList<>();
        try (DirectoryStream<Path> descriptors = Files.newDirectoryStream(
                Path.of("/proc/self/fd")
        )) {
            for (Path descriptor : descriptors) {
                if (targets.size() >= MAX_FILE_DESCRIPTORS) {
                    throw new IllegalStateException(
                            "file descriptor inspection exceeds the safety bound"
                    );
                }
                try {
                    targets.add(Files.readSymbolicLink(descriptor).toString());
                } catch (NoSuchFileException closedDuringInspection) {
                    // Descriptor closure races are expected; every still-open entry is inspected.
                }
            }
            return targets;
        } catch (IOException failure) {
            throw new IllegalStateException("cannot inspect guarded JVM file descriptors");
        }
    }

    private static boolean containsSocketMarker(String value) {
        String canonical = value.toLowerCase(Locale.ROOT);
        return SOCKET_MARKERS.stream().anyMatch(canonical::contains);
    }

    private static boolean isHostProxy(String value) {
        return value.toLowerCase(Locale.ROOT).contains("codecrow-host-proxy");
    }

    private static Set<String> unixSocketInodes(String table) {
        Set<String> inodes = new HashSet<>();
        for (String line : table.lines().skip(1).toList()) {
            String stripped = line.strip();
            if (stripped.isEmpty()) {
                continue;
            }
            String[] fields = stripped.split("\\s+");
            if (fields.length >= 7 && fields[6].chars().allMatch(Character::isDigit)) {
                inodes.add(fields[6]);
            }
        }
        return Set.copyOf(inodes);
    }

    private static Set<String> namedUnixSocketInodes(String table) {
        Set<String> inodes = new HashSet<>();
        for (String line : table.lines().skip(1).toList()) {
            String stripped = line.strip();
            if (stripped.isEmpty()) {
                continue;
            }
            String[] fields = stripped.split("\\s+");
            if (
                    fields.length >= 8
                            && fields[6].chars().allMatch(Character::isDigit)
            ) {
                inodes.add(fields[6]);
            }
        }
        return Set.copyOf(inodes);
    }

    record Snapshot(
            List<String> visiblePaths,
            String mountInfo,
            List<String> fileDescriptorTargets,
            String unixSocketTable
    ) {

        Snapshot(
                List<String> visiblePaths,
                String mountInfo,
                List<String> fileDescriptorTargets
        ) {
            this(visiblePaths, mountInfo, fileDescriptorTargets, "");
        }

        Snapshot {
            visiblePaths = List.copyOf(visiblePaths);
            mountInfo = mountInfo == null ? "" : mountInfo;
            fileDescriptorTargets = List.copyOf(fileDescriptorTargets);
            unixSocketTable = unixSocketTable == null ? "" : unixSocketTable;
        }
    }
}

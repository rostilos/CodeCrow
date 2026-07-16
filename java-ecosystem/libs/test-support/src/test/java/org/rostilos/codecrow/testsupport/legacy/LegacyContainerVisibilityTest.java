package org.rostilos.codecrow.testsupport.legacy;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.MockedStatic;

import java.io.IOException;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.CALLS_REAL_METHODS;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.mockStatic;
import static org.mockito.Mockito.when;

class LegacyContainerVisibilityTest {

    @TempDir
    Path directory;

    @Test
    void acceptsAHiddenCapabilityBridgeAndEmptyDockerSurface() {
        LegacyContainerVisibility.Snapshot snapshot = new LegacyContainerVisibility.Snapshot(
                List.of(),
                "36 24 0:32 / /run rw,nosuid - tmpfs tmpfs rw",
                List.of("pipe:[123]", "/dev/null")
        );

        assertThatCode(() -> LegacyContainerVisibility.assertHidden(
                Map.of("PATH", "/usr/bin"), snapshot
        ))
                .doesNotThrowAnyException();
    }

    @Test
    void rejectsVisibleSocketMountFdOrRuntimeVariables() {
        assertThatThrownBy(() -> LegacyContainerVisibility.assertHidden(
                Map.of(),
                new LegacyContainerVisibility.Snapshot(
                        List.of("/run/docker.sock"), "", List.of()
                )
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("docker.sock");

        assertThatThrownBy(() -> LegacyContainerVisibility.assertHidden(
                Map.of(),
                new LegacyContainerVisibility.Snapshot(
                        List.of(), "codecrow-host-proxy", List.of()
                )
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("proxy mount");

        assertThatThrownBy(() -> LegacyContainerVisibility.assertHidden(
                Map.of(),
                new LegacyContainerVisibility.Snapshot(
                        List.of(), "", List.of("socket:[1] -> /var/run/docker.sock")
                )
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("file descriptor");

        assertThatThrownBy(() -> LegacyContainerVisibility.assertHidden(
                Map.of("TESTCONTAINERS_HOST_OVERRIDE", "127.0.0.1"),
                new LegacyContainerVisibility.Snapshot(List.of(), "", List.of())
        )).isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("TESTCONTAINERS_HOST_OVERRIDE");

        for (String socket : List.of(
                "/run/containerd/containerd.sock",
                "/run/podman/podman.sock",
                "/var/run/docker.sock"
        )) {
            assertThatThrownBy(() -> LegacyContainerVisibility.assertHidden(
                    Map.of(),
                    new LegacyContainerVisibility.Snapshot(List.of(socket), "", List.of())
            )).isInstanceOf(IllegalStateException.class).hasMessageContaining("socket");
        }

        assertThatThrownBy(() -> LegacyContainerVisibility.assertHidden(
                Map.of("DOCKER_CONTEXT", "desktop-linux"),
                new LegacyContainerVisibility.Snapshot(List.of(), "", List.of())
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("DOCKER_CONTEXT");
    }

    @Test
    void rejectsNamedUnixSocketFdUsingProcInodeCorrelation() {
        LegacyContainerVisibility.Snapshot snapshot = new LegacyContainerVisibility.Snapshot(
                List.of(),
                "",
                List.of("socket:[4242]", "pipe:[19]"),
                "Num RefCount Protocol Flags Type St Inode Path\n"
                        + "000000: 00000002 00000000 00010000 0001 01 4242 "
                        + "/run/containerd/containerd.sock\n"
        );

        assertThatThrownBy(() -> LegacyContainerVisibility.assertHidden(Map.of(), snapshot))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("named Unix socket");
    }

    @Test
    void permitsOneJdkAnonymousWakeupSocketButRejectsMultipleEndpoints() {
        String oneSocket = "Num RefCount Protocol Flags Type St Inode Path\n"
                + "000000: 00000002 00000000 00000000 0001 03 4242\n";
        LegacyContainerVisibility.Snapshot expectedJdkWakeup =
                new LegacyContainerVisibility.Snapshot(
                        List.of(), "", List.of("socket:[4242]"), oneSocket
                );

        assertThatCode(() -> LegacyContainerVisibility.assertHidden(
                Map.of(), expectedJdkWakeup
        )).doesNotThrowAnyException();

        LegacyContainerVisibility.Snapshot multipleSockets =
                new LegacyContainerVisibility.Snapshot(
                        List.of(),
                        "",
                        List.of("socket:[4242]", "socket:[4343]"),
                        oneSocket + "000000: 00000002 00000000 00000000 0001 03 4343\n"
                );
        assertThatThrownBy(() -> LegacyContainerVisibility.assertHidden(
                Map.of(), multipleSockets
        )).isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("multiple anonymous Unix socket");
    }

    @Test
    void rejectsEveryControlSurfaceLocationAndCoversParserFallthroughs() {
        assertThatThrownBy(() -> LegacyContainerVisibility.assertHidden(
                Map.of(),
                new LegacyContainerVisibility.Snapshot(
                        List.of("/run/codecrow-host-proxy"), "", List.of()
                )
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("proxy mount");

        assertThatCode(() -> LegacyContainerVisibility.assertHidden(
                Map.of(),
                new LegacyContainerVisibility.Snapshot(
                        List.of("/run/harmless-capability"), "", List.of()
                )
        )).doesNotThrowAnyException();

        assertThatThrownBy(() -> LegacyContainerVisibility.assertHidden(
                Map.of(),
                new LegacyContainerVisibility.Snapshot(
                        List.of(), "/var/run/podman/podman.sock", List.of()
                )
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("mount table");

        assertThatThrownBy(() -> LegacyContainerVisibility.assertHidden(
                Map.of(),
                new LegacyContainerVisibility.Snapshot(
                        List.of(), "", List.of("/run/codecrow-host-proxy/control")
                )
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("file descriptor");

        String malformedTable = "header\n\n"
                + "short\n"
                + "000000: 00000002 00000000 00000000 0001 03 "
                + "not-a-number /tmp/ignored.sock\n";
        assertThatCode(() -> LegacyContainerVisibility.assertHidden(
                Map.of(),
                new LegacyContainerVisibility.Snapshot(
                        List.of(),
                        "",
                        List.of("plain-target", "socket:[999]"),
                        malformedTable
                )
        )).doesNotThrowAnyException();

        LegacyContainerVisibility.Snapshot nullText = new LegacyContainerVisibility.Snapshot(
                List.of(), null, List.of(), null
        );
        assertThat(nullText.mountInfo()).isEmpty();
        assertThat(nullText.unixSocketTable()).isEmpty();
    }

    @Test
    void captureRecordsAnExistingControlPath() {
        Path dockerSocket = Path.of("/run/docker.sock");
        try (MockedStatic<Files> files = mockStatic(Files.class, CALLS_REAL_METHODS)) {
            files.when(() -> Files.exists(dockerSocket, LinkOption.NOFOLLOW_LINKS))
                    .thenReturn(true);
            LegacyContainerVisibility.Snapshot snapshot = LegacyContainerVisibility.capture();
            assertThat(snapshot.visiblePaths()).contains(dockerSocket.toString());
        }
    }

    @Test
    void boundedReadersRejectOversizeAndIoFailures() throws Exception {
        Path content = Files.writeString(directory.resolve("bounded.txt"), "abcdef");
        assertThatThrownBy(() -> invokeStatic(
                "readBounded",
                new Class<?>[]{Path.class, int.class, String.class},
                content,
                3,
                "test data"
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("safety bound");

        assertThatThrownBy(() -> invokeStatic(
                "readBounded",
                new Class<?>[]{Path.class, int.class, String.class},
                directory.resolve("missing"),
                20,
                "test data"
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("cannot inspect");
    }

    @Test
    void descriptorReaderHandlesRacesBoundsAndDirectoryFailures() throws Throwable {
        @SuppressWarnings("unchecked")
        DirectoryStream<Path> raced = mock(DirectoryStream.class);
        when(raced.iterator()).thenReturn(List.of(Path.of("/proc/self/fd/9")).iterator());
        try (MockedStatic<Files> files = mockStatic(Files.class, CALLS_REAL_METHODS)) {
            files.when(() -> Files.newDirectoryStream(Path.of("/proc/self/fd")))
                    .thenReturn(raced);
            files.when(() -> Files.readSymbolicLink(Path.of("/proc/self/fd/9")))
                    .thenThrow(new NoSuchFileException("9"));
            @SuppressWarnings("unchecked")
            List<String> targets = (List<String>) invokeStatic(
                    "readFileDescriptors", new Class<?>[0]
            );
            assertThat(targets).isEmpty();
        }

        @SuppressWarnings("unchecked")
        DirectoryStream<Path> oversized = mock(DirectoryStream.class);
        List<Path> descriptors = new ArrayList<>();
        for (int index = 0; index <= 4096; index++) {
            descriptors.add(Path.of("/synthetic/fd/" + index));
        }
        when(oversized.iterator()).thenReturn(descriptors.iterator());
        try (MockedStatic<Files> files = mockStatic(Files.class, CALLS_REAL_METHODS)) {
            files.when(() -> Files.newDirectoryStream(Path.of("/proc/self/fd")))
                    .thenReturn(oversized);
            files.when(() -> Files.readSymbolicLink(any(Path.class)))
                    .thenReturn(Path.of("pipe:[1]"));
            assertThatThrownBy(() -> invokeStatic(
                    "readFileDescriptors", new Class<?>[0]
            )).isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("safety bound");
        }

        try (MockedStatic<Files> files = mockStatic(Files.class, CALLS_REAL_METHODS)) {
            files.when(() -> Files.newDirectoryStream(Path.of("/proc/self/fd")))
                    .thenThrow(new IOException("cannot list"));
            assertThatThrownBy(() -> invokeStatic(
                    "readFileDescriptors", new Class<?>[0]
            )).isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("file descriptors");
        }
    }

    private static Object invokeStatic(
            String name,
            Class<?>[] parameterTypes,
            Object... arguments
    ) throws Throwable {
        Method method = LegacyContainerVisibility.class.getDeclaredMethod(name, parameterTypes);
        method.setAccessible(true);
        try {
            return method.invoke(null, arguments);
        } catch (InvocationTargetException failure) {
            throw failure.getCause();
        }
    }
}

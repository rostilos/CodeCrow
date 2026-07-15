package org.rostilos.codecrow.testsupport.legacy;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.MockedStatic;

import java.io.IOException;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.nio.file.attribute.UserPrincipal;
import java.util.Collections;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.CALLS_REAL_METHODS;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.mockStatic;
import static org.mockito.Mockito.when;

class LegacyContainerSafePathsEdgeCasesTest {

    @TempDir
    Path directory;

    @Test
    void rejectsNonCanonicalAndWrongOwnerDirectories() throws Exception {
        Path synthetic = mock(Path.class);
        when(synthetic.isAbsolute()).thenReturn(true);
        when(synthetic.normalize()).thenReturn(synthetic);
        when(synthetic.getRoot()).thenReturn(Path.of("/"));
        when(synthetic.iterator()).thenReturn(Collections.emptyIterator());
        when(synthetic.toRealPath(LinkOption.NOFOLLOW_LINKS)).thenReturn(Path.of("/different"));

        try (MockedStatic<Files> files = mockStatic(Files.class, CALLS_REAL_METHODS)) {
            files.when(() -> Files.isDirectory(synthetic, LinkOption.NOFOLLOW_LINKS))
                    .thenReturn(true);
            assertThatThrownBy(() -> LegacyContainerSafePaths.requireTrustedDirectory(synthetic))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("canonical");
        }

        UserPrincipal otherOwner = mock(UserPrincipal.class);
        try (MockedStatic<Files> files = mockStatic(Files.class, CALLS_REAL_METHODS)) {
            files.when(() -> Files.getOwner(directory, LinkOption.NOFOLLOW_LINKS))
                    .thenReturn(otherOwner);
            assertThatThrownBy(() -> LegacyContainerSafePaths.requireTrustedDirectory(directory))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("owned");
        }
    }

    @Test
    void distinguishesWorldWriteFromStickyAncestors() throws Throwable {
        Path child = Files.createDirectory(directory.resolve("world-write"));
        Files.setPosixFilePermissions(child, PosixFilePermissions.fromString("rwx---rwx"));
        assertThatThrownBy(() -> LegacyContainerSafePaths.requireTrustedDirectory(child))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("writable ancestor");

        Path synthetic = Path.of("/synthetic-safe-path");
        try (MockedStatic<Files> files = mockStatic(Files.class, CALLS_REAL_METHODS)) {
            files.when(() -> Files.getAttribute(
                    Path.of("/"), "unix:mode", LinkOption.NOFOLLOW_LINKS
            )).thenReturn(0040777);
            files.when(() -> Files.getAttribute(
                    synthetic, "unix:mode", LinkOption.NOFOLLOW_LINKS
            )).thenReturn(0041777);
            assertThatCode(() -> invokeStatic(
                    "rejectUnsafeWritableAncestors",
                    new Class<?>[]{Path.class},
                    synthetic
            )).doesNotThrowAnyException();
        }
    }

    @Test
    void finalDirectoryPermissionCheckRejectsWorldWriteWithoutGroupWrite()
            throws Exception {
        Path synthetic = mock(Path.class);
        when(synthetic.isAbsolute()).thenReturn(true);
        when(synthetic.normalize()).thenReturn(synthetic);
        when(synthetic.getRoot()).thenReturn(Path.of("/"));
        when(synthetic.iterator()).thenReturn(Collections.emptyIterator());
        when(synthetic.toRealPath(LinkOption.NOFOLLOW_LINKS)).thenReturn(synthetic);
        UserPrincipal owner = mock(UserPrincipal.class);

        try (MockedStatic<Files> files = mockStatic(Files.class, CALLS_REAL_METHODS)) {
            files.when(() -> Files.isDirectory(synthetic, LinkOption.NOFOLLOW_LINKS))
                    .thenReturn(true);
            files.when(() -> Files.getPosixFilePermissions(
                    synthetic, LinkOption.NOFOLLOW_LINKS
            )).thenReturn(PosixFilePermissions.fromString("rwxrwx---"));
            files.when(() -> Files.getOwner(Path.of("/proc/self"))).thenReturn(owner);
            files.when(() -> Files.getOwner(synthetic, LinkOption.NOFOLLOW_LINKS))
                    .thenReturn(owner);

            assertThatThrownBy(() -> LegacyContainerSafePaths.requireTrustedDirectory(synthetic))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("group/world writable");

            files.when(() -> Files.getPosixFilePermissions(
                    synthetic, LinkOption.NOFOLLOW_LINKS
            )).thenReturn(PosixFilePermissions.fromString("rwx---rwx"));
            assertThatThrownBy(() -> LegacyContainerSafePaths.requireTrustedDirectory(synthetic))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("group/world writable");
        }
    }

    @Test
    void wrapsUnsupportedAndIoFilesystemValidationFailures() throws Exception {
        try (MockedStatic<Files> files = mockStatic(Files.class, CALLS_REAL_METHODS)) {
            files.when(() -> Files.getPosixFilePermissions(
                    directory, LinkOption.NOFOLLOW_LINKS
            )).thenThrow(new UnsupportedOperationException("no posix"));
            assertThatThrownBy(() -> LegacyContainerSafePaths.requireTrustedDirectory(directory))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("POSIX nofollow");
        }

        try (MockedStatic<Files> files = mockStatic(Files.class, CALLS_REAL_METHODS)) {
            files.when(() -> Files.getPosixFilePermissions(
                    directory, LinkOption.NOFOLLOW_LINKS
            )).thenThrow(new IOException("unreadable"));
            assertThatThrownBy(() -> LegacyContainerSafePaths.requireTrustedDirectory(directory))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("cannot resolve");
        }

        Path synthetic = Path.of("/synthetic-ancestor");
        try (MockedStatic<Files> files = mockStatic(Files.class, CALLS_REAL_METHODS)) {
            files.when(() -> Files.getAttribute(
                    Path.of("/"), "unix:mode", LinkOption.NOFOLLOW_LINKS
            )).thenThrow(new UnsupportedOperationException("no unix mode"));
            files.when(() -> Files.getAttribute(
                    synthetic, "unix:mode", LinkOption.NOFOLLOW_LINKS
            )).thenThrow(new UnsupportedOperationException("no unix mode"));
            assertThatThrownBy(() -> invokeStatic(
                    "rejectUnsafeWritableAncestors",
                    new Class<?>[]{Path.class},
                    synthetic
            )).isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("Unix mode validation");
        }

        try (MockedStatic<Files> files = mockStatic(Files.class, CALLS_REAL_METHODS)) {
            files.when(() -> Files.getAttribute(
                    Path.of("/"), "unix:mode", LinkOption.NOFOLLOW_LINKS
            )).thenThrow(new IOException("cannot stat"));
            files.when(() -> Files.getAttribute(
                    synthetic, "unix:mode", LinkOption.NOFOLLOW_LINKS
            )).thenThrow(new IOException("cannot stat"));
            assertThatThrownBy(() -> invokeStatic(
                    "rejectUnsafeWritableAncestors",
                    new Class<?>[]{Path.class},
                    synthetic
            )).isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("cannot inspect");
        }
    }

    @Test
    void privateWalkersStillRejectRelativeInputs() {
        assertThatThrownBy(() -> invokeStatic(
                "rejectSymlinkComponents",
                new Class<?>[]{Path.class},
                Path.of("relative")
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("absolute");
        assertThatThrownBy(() -> invokeStatic(
                "rejectUnsafeWritableAncestors",
                new Class<?>[]{Path.class},
                Path.of("relative")
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("absolute");
    }

    private static Object invokeStatic(
            String name,
            Class<?>[] parameterTypes,
            Object... arguments
    ) throws Throwable {
        Method method = LegacyContainerSafePaths.class.getDeclaredMethod(name, parameterTypes);
        method.setAccessible(true);
        try {
            return method.invoke(null, arguments);
        } catch (InvocationTargetException failure) {
            throw failure.getCause();
        }
    }
}

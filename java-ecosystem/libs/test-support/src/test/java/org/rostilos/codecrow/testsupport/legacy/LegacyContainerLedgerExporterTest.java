package org.rostilos.codecrow.testsupport.legacy;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.MockedStatic;
import org.rostilos.codecrow.testsupport.offline.ExternalCallLedger;

import java.io.IOException;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.ByteBuffer;
import java.nio.channels.SeekableByteChannel;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.SecureDirectoryStream;
import java.nio.file.attribute.BasicFileAttributes;
import java.nio.file.attribute.PosixFileAttributeView;
import java.nio.file.attribute.PosixFileAttributes;
import java.nio.file.attribute.PosixFilePermissions;
import java.nio.file.attribute.UserPrincipal;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.CALLS_REAL_METHODS;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.mockStatic;
import static org.mockito.Mockito.when;

class LegacyContainerLedgerExporterTest {

    @TempDir
    Path directory;

    @Test
    void reservesANewRegularDestinationAndExportsTheCanonicalLedger() throws Exception {
        Path destination = directory.resolve("legacy-container-it-queue-safe_run_01.json");
        ExternalCallLedger ledger = new ExternalCallLedger();

        new LegacyContainerLedgerExporter(ledger).export(destination);

        assertThat(destination).isRegularFile();
        assertThat(Files.isSymbolicLink(destination)).isFalse();
        assertThat(Files.getPosixFilePermissions(destination))
                .isEqualTo(PosixFilePermissions.fromString("rw-------"));
        assertThat(Files.getOwner(destination)).isEqualTo(Files.getOwner(directory));
        assertThat(Files.readString(destination))
                .contains("\"schema_version\"")
                .contains("\"live_call_count\"");
    }

    @Test
    void refusesExistingFilesAndSymlinksWithoutTouchingTheirContent() throws Exception {
        LegacyContainerLedgerExporter exporter =
                new LegacyContainerLedgerExporter(new ExternalCallLedger());
        Path existing = Files.writeString(directory.resolve("existing.json"), "keep-existing");
        assertThatThrownBy(() -> exporter.export(existing))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("already exists");
        assertThat(Files.readString(existing)).isEqualTo("keep-existing");

        Path target = Files.writeString(directory.resolve("target.json"), "keep-target");
        Path link = directory.resolve("linked.json");
        Files.createSymbolicLink(link, target);
        assertThatThrownBy(() -> exporter.export(link))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("already exists");
        assertThat(Files.readString(target)).isEqualTo("keep-target");
        assertThat(Files.isSymbolicLink(link)).isTrue();
    }

    @Test
    void rejectsAParentPathSwappedForASymlinkBeforeExport() throws Exception {
        Path originalParent = Files.createDirectory(directory.resolve("owned-ledgers"));
        Path destination = originalParent.resolve("result.json");
        Path movedParent = directory.resolve("moved-ledgers");
        Files.move(originalParent, movedParent);
        Files.createSymbolicLink(originalParent, movedParent);

        assertThatThrownBy(() -> new LegacyContainerLedgerExporter(
                new ExternalCallLedger()
        ).export(destination))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("symlink");
        assertThat(movedParent.resolve("result.json")).doesNotExist();
    }

    @Test
    void removesAPartialFileWhenTheLedgerWriterFails() throws Exception {
        Path destination = directory.resolve("partial.json");
        LegacyContainerLedgerExporter exporter = new LegacyContainerLedgerExporter(channel -> {
            channel.write(ByteBuffer.wrap(new byte[]{'{'}));
            throw new IOException("injected write failure");
        });

        assertThatThrownBy(() -> exporter.export(destination))
                .isInstanceOf(IOException.class)
                .hasMessage("injected write failure");
        assertThat(destination).doesNotExist();
    }

    @Test
    void rejectsAndRemovesAWriterTamperedFinalMode() throws Exception {
        Path destination = directory.resolve("tampered-mode.json");
        LegacyContainerLedgerExporter exporter = new LegacyContainerLedgerExporter(channel -> {
            channel.write(ByteBuffer.wrap("{}".getBytes(java.nio.charset.StandardCharsets.UTF_8)));
            Files.setPosixFilePermissions(
                    destination,
                    PosixFilePermissions.fromString("rw-r--r--")
            );
        });

        assertThatThrownBy(() -> exporter.export(destination))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("0600");
        assertThat(destination).doesNotExist();
    }

    @Test
    void detectsAParentSwapDuringHandleRelativeExportAndCleansTheOpenedDirectory()
            throws Exception {
        Path originalParent = Files.createDirectory(directory.resolve("race-parent"));
        Files.setPosixFilePermissions(
                originalParent,
                PosixFilePermissions.fromString("rwx------")
        );
        Path movedParent = directory.resolve("race-parent-moved");
        Path destination = originalParent.resolve("result.json");
        LegacyContainerLedgerExporter exporter = new LegacyContainerLedgerExporter(channel -> {
            channel.write(ByteBuffer.wrap("{}".getBytes(java.nio.charset.StandardCharsets.UTF_8)));
            Files.move(originalParent, movedParent);
            Files.createSymbolicLink(originalParent, movedParent);
        });

        assertThatThrownBy(() -> exporter.export(destination))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("parent changed");
        assertThat(movedParent.resolve("result.json")).doesNotExist();
        assertThat(Files.isSymbolicLink(originalParent)).isTrue();
    }

    @Test
    void rejectsDestinationsWithoutIdentityCanonicalityOrSecureDirectoryHandles()
            throws Exception {
        LegacyContainerLedgerExporter exporter = new LegacyContainerLedgerExporter(channel -> { });
        assertThatThrownBy(() -> exporter.export(Path.of("/")))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("no parent");

        Path syntheticDestination = mock(Path.class);
        Path syntheticAbsolute = mock(Path.class);
        when(syntheticDestination.toAbsolutePath()).thenReturn(syntheticAbsolute);
        when(syntheticAbsolute.normalize()).thenReturn(syntheticAbsolute);
        when(syntheticAbsolute.getParent()).thenReturn(directory);
        when(syntheticAbsolute.getFileName()).thenReturn(null);
        assertThatThrownBy(() -> exporter.export(syntheticDestination))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("no parent");

        Path destination = directory.resolve("canonical.json");
        try (MockedStatic<LegacyContainerSafePaths> paths = mockStatic(
                LegacyContainerSafePaths.class
        )) {
            paths.when(() -> LegacyContainerSafePaths.requireTrustedDirectory(directory))
                    .thenReturn(directory.resolve("different"));
            assertThatThrownBy(() -> exporter.export(destination))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("not canonical");
        }

        BasicFileAttributes noIdentity = mock(BasicFileAttributes.class);
        try (MockedStatic<LegacyContainerSafePaths> paths = mockStatic(
                LegacyContainerSafePaths.class
        ); MockedStatic<Files> files = mockStatic(Files.class, CALLS_REAL_METHODS)) {
            paths.when(() -> LegacyContainerSafePaths.requireTrustedDirectory(directory))
                    .thenReturn(directory);
            files.when(() -> Files.readAttributes(
                    directory,
                    BasicFileAttributes.class,
                    LinkOption.NOFOLLOW_LINKS
            )).thenReturn(noIdentity);
            assertThatThrownBy(() -> exporter.export(directory.resolve("identity.json")))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("stable identity");
        }

        @SuppressWarnings("unchecked")
        DirectoryStream<Path> ordinary = mock(DirectoryStream.class);
        try (MockedStatic<Files> files = mockStatic(Files.class, CALLS_REAL_METHODS)) {
            files.when(() -> Files.newDirectoryStream(directory)).thenReturn(ordinary);
            assertThatThrownBy(() -> exporter.export(directory.resolve("ordinary.json")))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("secure handle-relative");
        }
    }

    @Test
    void rejectsMissingNonRegularSymlinkAndWrongOwnerAttributes() throws Throwable {
        @SuppressWarnings("unchecked")
        SecureDirectoryStream<Path> secure = mock(SecureDirectoryStream.class);
        Path fileName = Path.of("ledger.json");
        when(secure.getFileAttributeView(
                fileName,
                PosixFileAttributeView.class,
                LinkOption.NOFOLLOW_LINKS
        )).thenReturn(null);
        assertThatThrownBy(() -> invokeStatic(
                "assertPrivateRegularFile",
                new Class<?>[]{SecureDirectoryStream.class, Path.class, Path.class},
                secure,
                fileName,
                directory
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("POSIX attributes");

        PosixFileAttributeView view = mock(PosixFileAttributeView.class);
        PosixFileAttributes attributes = mock(PosixFileAttributes.class);
        when(secure.getFileAttributeView(
                fileName,
                PosixFileAttributeView.class,
                LinkOption.NOFOLLOW_LINKS
        )).thenReturn(view);
        when(view.readAttributes()).thenReturn(attributes);
        when(attributes.isRegularFile()).thenReturn(false);
        assertThatThrownBy(() -> invokeStatic(
                "assertPrivateRegularFile",
                new Class<?>[]{SecureDirectoryStream.class, Path.class, Path.class},
                secure,
                fileName,
                directory
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("regular file");

        when(attributes.isRegularFile()).thenReturn(true);
        when(attributes.isSymbolicLink()).thenReturn(true);
        assertThatThrownBy(() -> invokeStatic(
                "assertPrivateRegularFile",
                new Class<?>[]{SecureDirectoryStream.class, Path.class, Path.class},
                secure,
                fileName,
                directory
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("regular file");

        when(attributes.isSymbolicLink()).thenReturn(false);
        when(attributes.permissions()).thenReturn(
                PosixFilePermissions.fromString("rw-------")
        );
        when(attributes.owner()).thenReturn(mock(UserPrincipal.class));
        assertThatThrownBy(() -> invokeStatic(
                "assertPrivateRegularFile",
                new Class<?>[]{SecureDirectoryStream.class, Path.class, Path.class},
                secure,
                fileName,
                directory
        )).isInstanceOf(IllegalStateException.class).hasMessageContaining("owner");
    }

    @Test
    void cleanupFailureIsSuppressedAndEveryThrowableShapeIsPreserved() throws Throwable {
        RuntimeException writeFailure = new IllegalStateException("write failed");
        Error cleanupFailure = new AssertionError("cleanup failed");
        @SuppressWarnings("unchecked")
        SecureDirectoryStream<Path> secure = mock(SecureDirectoryStream.class);
        SeekableByteChannel channel = mock(SeekableByteChannel.class);
        when(secure.newByteChannel(any(Path.class), any(Set.class), any()))
                .thenReturn(channel);
        org.mockito.Mockito.doThrow(cleanupFailure)
                .when(secure).deleteFile(Path.of("failed.json"));
        LegacyContainerLedgerExporter exporter = new LegacyContainerLedgerExporter(ignored -> {
            throw writeFailure;
        });

        assertThatThrownBy(() -> invokeInstance(
                exporter,
                "exportThrough",
                new Class<?>[]{
                        SecureDirectoryStream.class,
                        Path.class,
                        Path.class,
                        BasicFileAttributes.class
                },
                secure,
                Path.of("failed.json"),
                directory,
                mock(BasicFileAttributes.class)
        )).isSameAs(writeFailure)
                .satisfies(failure -> assertThat(failure.getSuppressed())
                        .containsExactly(cleanupFailure));

        Error error = new AssertionError("fatal");
        assertThatThrownBy(() -> invokeStatic(
                "rethrowable", new Class<?>[]{Throwable.class}, error
        )).isSameAs(error);
        IOException io = new IOException("io");
        assertThatThrownBy(() -> invokeStatic(
                "rethrowable", new Class<?>[]{Throwable.class}, io
        )).isSameAs(io);
        Object wrapped = invokeStatic(
                "rethrowable", new Class<?>[]{Throwable.class}, new Throwable("checked")
        );
        assertThat(wrapped).isInstanceOf(IllegalStateException.class);
        assertThat(((Throwable) wrapped).getCause()).isInstanceOf(Throwable.class);
    }

    private static Object invokeStatic(
            String name,
            Class<?>[] parameterTypes,
            Object... arguments
    ) throws Throwable {
        Method method = LegacyContainerLedgerExporter.class.getDeclaredMethod(
                name, parameterTypes
        );
        method.setAccessible(true);
        return invoke(method, null, arguments);
    }

    private static Object invokeInstance(
            Object target,
            String name,
            Class<?>[] parameterTypes,
            Object... arguments
    ) throws Throwable {
        Method method = target.getClass().getDeclaredMethod(name, parameterTypes);
        method.setAccessible(true);
        return invoke(method, target, arguments);
    }

    private static Object invoke(Method method, Object target, Object... arguments)
            throws Throwable {
        try {
            return method.invoke(target, arguments);
        } catch (InvocationTargetException failure) {
            throw failure.getCause();
        }
    }
}

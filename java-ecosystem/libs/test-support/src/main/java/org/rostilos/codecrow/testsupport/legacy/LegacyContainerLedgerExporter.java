package org.rostilos.codecrow.testsupport.legacy;

import org.rostilos.codecrow.testsupport.offline.ExternalCallLedger;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.SeekableByteChannel;
import java.nio.file.DirectoryStream;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.OpenOption;
import java.nio.file.Path;
import java.nio.file.SecureDirectoryStream;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.BasicFileAttributes;
import java.nio.file.attribute.PosixFileAttributeView;
import java.nio.file.attribute.PosixFileAttributes;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.Objects;
import java.util.Set;

/** Writes the final ledger through a handle-relative, create-new destination. */
final class LegacyContainerLedgerExporter {

    private static final Set<OpenOption> CREATE_OPTIONS = Set.of(
            StandardOpenOption.CREATE_NEW,
            StandardOpenOption.WRITE,
            LinkOption.NOFOLLOW_LINKS
    );

    private final LedgerWriter writer;

    LegacyContainerLedgerExporter(ExternalCallLedger ledger) {
        Objects.requireNonNull(ledger, "ledger");
        this.writer = channel -> writeFully(channel, ledger.canonicalJsonBytes());
    }

    LegacyContainerLedgerExporter(LedgerWriter writer) {
        this.writer = Objects.requireNonNull(writer, "writer");
    }

    void export(Path destination) throws IOException {
        Objects.requireNonNull(destination, "destination");
        Path absolute = destination.toAbsolutePath().normalize();
        Path parent = absolute.getParent();
        if (parent == null || absolute.getFileName() == null) {
            throw new IllegalStateException("external-call ledger destination has no parent");
        }
        Path trustedParent = LegacyContainerSafePaths.requireTrustedDirectory(parent);
        if (!trustedParent.resolve(absolute.getFileName()).equals(absolute)) {
            throw new IllegalStateException("external-call ledger destination is not canonical");
        }

        BasicFileAttributes parentBefore = readBasic(trustedParent);
        if (parentBefore.fileKey() == null) {
            throw new IllegalStateException("external-call ledger parent has no stable identity");
        }
        try (DirectoryStream<Path> directory = Files.newDirectoryStream(trustedParent)) {
            if (!(directory instanceof SecureDirectoryStream<?>)) {
                throw new IllegalStateException(
                        "external-call ledger directory requires secure handle-relative access"
                );
            }
            @SuppressWarnings("unchecked")
            SecureDirectoryStream<Path> secure = (SecureDirectoryStream<Path>) directory;
            exportThrough(secure, absolute.getFileName(), trustedParent, parentBefore);
        }
    }

    private void exportThrough(
            SecureDirectoryStream<Path> directory,
            Path fileName,
            Path parent,
            BasicFileAttributes parentBefore
    ) throws IOException {
        boolean created = false;
        Throwable failure = null;
        try {
            try (SeekableByteChannel channel = directory.newByteChannel(
                    fileName,
                    CREATE_OPTIONS,
                    PosixFilePermissions.asFileAttribute(
                            PosixFilePermissions.fromString("rw-------")
                    )
            )) {
                created = true;
                writer.write(channel);
            }
            assertStableParent(parent, parentBefore);
            assertPrivateRegularFile(directory, fileName, parent);
            return;
        } catch (FileAlreadyExistsException existing) {
            failure = new IllegalStateException(
                    "external-call ledger destination already exists",
                    existing
            );
        } catch (Throwable exportFailure) {
            failure = exportFailure;
        }

        if (created) {
            try {
                directory.deleteFile(fileName);
            } catch (Throwable cleanupFailure) {
                failure.addSuppressed(cleanupFailure);
            }
        }
        throw rethrowable(failure);
    }

    private static void assertStableParent(
            Path parent,
            BasicFileAttributes before
    ) throws IOException {
        BasicFileAttributes after = readBasic(parent);
        if (!Objects.equals(before.fileKey(), after.fileKey())) {
            throw new IllegalStateException(
                    "external-call ledger parent changed during export"
            );
        }
    }

    private static void assertPrivateRegularFile(
            SecureDirectoryStream<Path> directory,
            Path fileName,
            Path parent
    ) throws IOException {
        PosixFileAttributeView view = directory.getFileAttributeView(
                fileName,
                PosixFileAttributeView.class,
                LinkOption.NOFOLLOW_LINKS
        );
        if (view == null) {
            throw new IllegalStateException("external-call ledger requires POSIX attributes");
        }
        PosixFileAttributes attributes = view.readAttributes();
        if (!attributes.isRegularFile() || attributes.isSymbolicLink()) {
            throw new IllegalStateException(
                    "external-call ledger export did not produce a regular file"
            );
        }
        if (!attributes.permissions().equals(PosixFilePermissions.fromString("rw-------"))) {
            throw new IllegalStateException(
                    "external-call ledger file must have private mode 0600"
            );
        }
        if (!attributes.owner().equals(Files.getOwner(parent, LinkOption.NOFOLLOW_LINKS))) {
            throw new IllegalStateException(
                    "external-call ledger file owner does not match its trusted parent"
            );
        }
    }

    private static BasicFileAttributes readBasic(Path path) throws IOException {
        return Files.readAttributes(
                path,
                BasicFileAttributes.class,
                LinkOption.NOFOLLOW_LINKS
        );
    }

    private static void writeFully(SeekableByteChannel channel, byte[] document)
            throws IOException {
        ByteBuffer buffer = ByteBuffer.wrap(document);
        while (buffer.hasRemaining()) {
            channel.write(buffer);
        }
    }

    private static RuntimeException rethrowable(Throwable failure) throws IOException {
        if (failure instanceof Error error) {
            throw error;
        }
        if (failure instanceof RuntimeException runtime) {
            return runtime;
        }
        if (failure instanceof IOException io) {
            throw io;
        }
        return new IllegalStateException("external-call ledger export failed", failure);
    }

    @FunctionalInterface
    interface LedgerWriter {

        void write(SeekableByteChannel channel) throws Exception;
    }
}

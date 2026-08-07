package org.rostilos.codecrow.ragengine.source;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.channels.SeekableByteChannel;
import java.nio.file.FileVisitResult;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.OpenOption;
import java.nio.file.Path;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.BasicFileAttributes;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Set;

/**
 * Canonical content identity for a normalized repository source tree.
 *
 * <p>The digest intentionally excludes Git's administrative directory. It
 * includes every other regular file and symlink using repository-relative
 * paths and exact bytes, matching the RAG consumer's verifier.</p>
 */
public final class RepositorySourceTreeIdentity {
    private static final byte[] SCHEMA =
            "codecrow.repository-source-tree".getBytes(StandardCharsets.US_ASCII);

    private RepositorySourceTreeIdentity() {
    }

    public static String sha256(Path repositoryRoot) throws IOException {
        Path root = repositoryRoot.toAbsolutePath().normalize();
        if (!Files.isDirectory(root, LinkOption.NOFOLLOW_LINKS)) {
            throw new IOException("Repository source path is not a directory: " + root);
        }

        List<Entry> entries = new ArrayList<>();
        Files.walkFileTree(root, new SimpleFileVisitor<>() {
            @Override
            public FileVisitResult preVisitDirectory(
                    Path directory,
                    BasicFileAttributes attributes
            ) {
                if (!directory.equals(root)
                        && ".git".equals(root.relativize(directory).getName(0).toString())) {
                    return FileVisitResult.SKIP_SUBTREE;
                }
                return FileVisitResult.CONTINUE;
            }

            @Override
            public FileVisitResult visitFile(Path file, BasicFileAttributes attributes)
                    throws IOException {
                Path relative = root.relativize(file);
                if (relative.getNameCount() > 0
                        && ".git".equals(relative.getName(0).toString())) {
                    return FileVisitResult.CONTINUE;
                }
                if (attributes.isSymbolicLink()) {
                    entries.add(new Entry("symlink", relative, Files.readSymbolicLink(file)));
                } else if (attributes.isRegularFile()) {
                    entries.add(new Entry("file", relative, file));
                } else {
                    throw new IOException(
                            "Repository source contains an unsupported filesystem entry: "
                                    + relative.toString().replace('\\', '/'));
                }
                return FileVisitResult.CONTINUE;
            }
        });
        entries.sort(Comparator.comparing(
                entry -> entry.relativePath().toString().replace('\\', '/')
                        .getBytes(StandardCharsets.UTF_8),
                RepositorySourceTreeIdentity::compareUnsigned
        ));

        MessageDigest digest = newDigest();
        feedFramed(digest, SCHEMA);
        byte[] buffer = new byte[1024 * 1024];
        for (Entry entry : entries) {
            feedFramed(digest, entry.kind().getBytes(StandardCharsets.US_ASCII));
            feedFramed(
                    digest,
                    entry.relativePath().toString().replace('\\', '/')
                            .getBytes(StandardCharsets.UTF_8)
            );
            if ("symlink".equals(entry.kind())) {
                feedFramed(
                        digest,
                        entry.value().toString().getBytes(StandardCharsets.UTF_8)
                );
                continue;
            }

            BasicFileAttributes attributes = Files.readAttributes(
                    entry.value(),
                    BasicFileAttributes.class,
                    LinkOption.NOFOLLOW_LINKS
            );
            if (!attributes.isRegularFile()) {
                throw new IOException(
                        "Repository source entry changed or is not a regular file: "
                                + entry.relativePath().toString().replace('\\', '/'));
            }
            long expectedSize = attributes.size();
            digest.update(longBytes(expectedSize));
            long observedSize = 0;
            Set<OpenOption> options = Set.of(
                    StandardOpenOption.READ,
                    LinkOption.NOFOLLOW_LINKS
            );
            try (SeekableByteChannel channel = Files.newByteChannel(
                    entry.value(),
                    options
            )) {
                ByteBuffer byteBuffer = ByteBuffer.wrap(buffer);
                int count;
                while ((count = channel.read(byteBuffer)) != -1) {
                    if (count > 0) {
                        digest.update(buffer, 0, count);
                        observedSize += count;
                    }
                    byteBuffer.clear();
                }
            }
            if (observedSize != expectedSize) {
                throw new IOException(
                        "Repository source changed while it was being attested: "
                                + entry.relativePath().toString().replace('\\', '/'));
            }
        }
        digest.update(longBytes(entries.size()));
        return toHex(digest.digest());
    }

    private static int compareUnsigned(byte[] left, byte[] right) {
        int length = Math.min(left.length, right.length);
        for (int index = 0; index < length; index++) {
            int comparison = Integer.compare(
                    Byte.toUnsignedInt(left[index]),
                    Byte.toUnsignedInt(right[index])
            );
            if (comparison != 0) {
                return comparison;
            }
        }
        return Integer.compare(left.length, right.length);
    }

    private static void feedFramed(MessageDigest digest, byte[] value) {
        digest.update(longBytes(value.length));
        digest.update(value);
    }

    private static byte[] longBytes(long value) {
        return ByteBuffer.allocate(Long.BYTES).putLong(value).array();
    }

    private static MessageDigest newDigest() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }

    private static String toHex(byte[] bytes) {
        StringBuilder result = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) {
            result.append(Character.forDigit((value >>> 4) & 0xf, 16));
            result.append(Character.forDigit(value & 0xf, 16));
        }
        return result.toString();
    }

    private record Entry(String kind, Path relativePath, Path value) {
    }
}

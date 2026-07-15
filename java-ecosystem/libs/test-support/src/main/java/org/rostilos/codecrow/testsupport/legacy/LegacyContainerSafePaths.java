package org.rostilos.codecrow.testsupport.legacy;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermission;
import java.nio.file.attribute.PosixFilePermissions;
import java.nio.file.attribute.UserPrincipal;
import java.util.Set;

/** Nofollow validation shared by contract parsing and final ledger export. */
final class LegacyContainerSafePaths {

    private LegacyContainerSafePaths() {
    }

    static Path requireTrustedDirectory(Path candidate) {
        if (!candidate.isAbsolute()) {
            throw new IllegalStateException("external-call ledger directory must be absolute");
        }
        Path normalized = candidate.normalize();
        rejectSymlinkComponents(normalized);
        if (!Files.isDirectory(normalized, LinkOption.NOFOLLOW_LINKS)) {
            throw new IllegalStateException(
                    "external-call ledger directory must be an existing directory"
            );
        }
        rejectUnsafeWritableAncestors(normalized);
        try {
            Path real = normalized.toRealPath(LinkOption.NOFOLLOW_LINKS);
            if (!real.equals(normalized)) {
                throw new IllegalStateException(
                        "external-call ledger directory must be canonical"
                );
            }
            Set<PosixFilePermission> permissions = Files.getPosixFilePermissions(
                    real,
                    LinkOption.NOFOLLOW_LINKS
            );
            if (permissions.contains(PosixFilePermission.GROUP_WRITE)
                    || permissions.contains(PosixFilePermission.OTHERS_WRITE)) {
                throw new IllegalStateException(
                        "external-call ledger directory must not be group/world writable"
                );
            }
            if (!permissions.equals(PosixFilePermissions.fromString("rwx------"))) {
                throw new IllegalStateException(
                        "external-call ledger directory must have private mode 0700"
                );
            }
            // /proc/self is itself a root-owned symlink. Follow that one kernel-provided
            // link so the owner comes from this process' proc directory, while every
            // caller-controlled ledger path remains NOFOLLOW-validated above.
            UserPrincipal processOwner = Files.getOwner(Path.of("/proc/self"));
            UserPrincipal directoryOwner = Files.getOwner(real, LinkOption.NOFOLLOW_LINKS);
            if (!processOwner.equals(directoryOwner)) {
                throw new IllegalStateException(
                        "external-call ledger directory must be owned by the guarded process"
                );
            }
            return real;
        } catch (UnsupportedOperationException unsupported) {
            throw new IllegalStateException(
                    "external-call ledger directory requires POSIX nofollow validation"
            );
        } catch (IOException failure) {
            throw new IllegalStateException("cannot resolve external-call ledger directory");
        }
    }

    private static void rejectSymlinkComponents(Path path) {
        Path current = path.getRoot();
        if (current == null) {
            throw new IllegalStateException("external-call ledger directory must be absolute");
        }
        for (Path component : path) {
            current = current.resolve(component);
            if (Files.isSymbolicLink(current)) {
                throw new IllegalStateException(
                        "external-call ledger directory contains a symlink component"
                );
            }
        }
    }

    private static void rejectUnsafeWritableAncestors(Path path) {
        Path current = path.getRoot();
        if (current == null) {
            throw new IllegalStateException("external-call ledger directory must be absolute");
        }
        for (Path component : path) {
            current = current.resolve(component);
            try {
                int mode = ((Number) Files.getAttribute(
                        current,
                        "unix:mode",
                        LinkOption.NOFOLLOW_LINKS
                )).intValue();
                boolean groupOrWorldWritable = (mode & 0022) != 0;
                boolean sticky = (mode & 01000) != 0;
                if (groupOrWorldWritable && !sticky) {
                    throw new IllegalStateException(
                            "external-call ledger path has an unsafe writable ancestor"
                    );
                }
            } catch (UnsupportedOperationException unsupported) {
                throw new IllegalStateException(
                        "external-call ledger directory requires Unix mode validation"
                );
            } catch (IOException failure) {
                throw new IllegalStateException(
                        "cannot inspect external-call ledger directory ancestors"
                );
            }
        }
    }
}

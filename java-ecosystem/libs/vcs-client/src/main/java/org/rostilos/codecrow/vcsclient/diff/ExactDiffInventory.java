package org.rostilos.codecrow.vcsclient.diff;

import java.util.List;
import java.util.Objects;

/**
 * Provider-neutral inventory derived from one exact raw diff artifact.
 */
public record ExactDiffInventory(
        RawDiffProvenance provenance,
        List<Entry> entries,
        Completeness completeness,
        List<Gap> gaps
) {

    public ExactDiffInventory {
        provenance = Objects.requireNonNull(provenance, "provenance");
        entries = List.copyOf(Objects.requireNonNull(entries, "entries"));
        completeness = Objects.requireNonNull(completeness, "completeness");
        gaps = List.copyOf(Objects.requireNonNull(gaps, "gaps"));
    }

    public enum Completeness {
        COMPLETE,
        INCOMPLETE
    }

    public enum ChangeStatus {
        ADD,
        MODIFY,
        DELETE,
        RENAME,
        COPY
    }

    public enum GapType {
        MALFORMED,
        PROVIDER_TRUNCATED,
        PATCH_UNAVAILABLE
    }

    public record RawDiffProvenance(String algorithm, String digest, int utf8ByteLength) {

        public RawDiffProvenance {
            algorithm = Objects.requireNonNull(algorithm, "algorithm");
            digest = Objects.requireNonNull(digest, "digest");
            if (utf8ByteLength < 0) {
                throw new IllegalArgumentException("utf8ByteLength must not be negative");
            }
        }
    }

    public record Entry(
            String oldPath,
            String newPath,
            ChangeStatus status,
            List<Hunk> hunks,
            boolean binary,
            String oldMode,
            String newMode,
            String rawPatchSha256
    ) {

        public Entry {
            if (oldPath == null && newPath == null) {
                throw new IllegalArgumentException("An entry must retain an old or new path");
            }
            status = Objects.requireNonNull(status, "status");
            hunks = List.copyOf(Objects.requireNonNull(hunks, "hunks"));
            rawPatchSha256 = Objects.requireNonNull(rawPatchSha256, "rawPatchSha256");
        }
    }

    public record Hunk(LineRange oldRange, LineRange newRange) {

        public Hunk {
            oldRange = Objects.requireNonNull(oldRange, "oldRange");
            newRange = Objects.requireNonNull(newRange, "newRange");
        }
    }

    public record LineRange(int start, int lineCount) {

        public LineRange {
            if (start < 0) {
                throw new IllegalArgumentException("start must not be negative");
            }
            if (lineCount < 0) {
                throw new IllegalArgumentException("lineCount must not be negative");
            }
        }
    }

    public record Gap(GapType type, String detail) {

        public Gap {
            type = Objects.requireNonNull(type, "type");
            detail = Objects.requireNonNull(detail, "detail");
        }
    }
}

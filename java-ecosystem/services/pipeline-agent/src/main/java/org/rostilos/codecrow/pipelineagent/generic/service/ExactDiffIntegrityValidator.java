package org.rostilos.codecrow.pipelineagent.generic.service;

import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.rostilos.codecrow.analysisengine.util.DiffParsingUtils;
import org.rostilos.codecrow.analysisengine.util.ExactDiffParser;
import org.rostilos.codecrow.pipelineagent.generic.service.AbstractVcsAiClientService.ExpectedFileChange;
import org.rostilos.codecrow.pipelineagent.generic.service.AbstractVcsAiClientService.PullRequestMetadata;

/** Verifies structural and provider-backed completeness of an exact diff. */
final class ExactDiffIntegrityValidator {
    private ExactDiffIntegrityValidator() {}

    static void validate(PullRequestMetadata metadata, String exactDiff) throws IOException {
        List<DiffParsingUtils.FileChange> actualChanges;
        try {
            actualChanges = ExactDiffParser.parse(exactDiff);
        } catch (IllegalArgumentException invalidDiff) {
            throw new IOException("Provider returned an unparseable exact diff", invalidDiff);
        }

        Map<String, DiffLineCounts> actualByPath = new LinkedHashMap<>();
        for (DiffParsingUtils.FileChange change : actualChanges) {
            String path = change.changeType() == DiffParsingUtils.ChangeType.DELETED
                    ? change.oldPath() : change.newPath();
            if (actualByPath.put(path, countHunkLines(change.diff())) != null) {
                throw new IOException("Provider exact diff contains duplicate file path: " + path);
            }
        }
        if (!metadata.exactInventoryAvailable()) return;

        Map<String, ExpectedFileChange> expectedByPath = new LinkedHashMap<>();
        for (ExpectedFileChange expected : metadata.expectedFileChanges()) {
            if (expectedByPath.put(expected.path(), expected) != null) {
                throw new IOException(
                        "Provider changed-file inventory contains duplicate path: " + expected.path());
            }
        }
        if (!actualByPath.keySet().equals(expectedByPath.keySet())) {
            throw new IOException("Provider exact diff does not match its changed-file inventory");
        }
        for (ExpectedFileChange expected : metadata.expectedFileChanges()) {
            DiffLineCounts actual = actualByPath.get(expected.path());
            if (actual.additions() != expected.additions()
                    || actual.deletions() != expected.deletions()) {
                throw new IOException(
                        "Provider exact diff line counts do not match inventory for "
                                + expected.path());
            }
        }
    }

    private static DiffLineCounts countHunkLines(String section) throws IOException {
        long additions = 0;
        long deletions = 0;
        long oldRemaining = 0;
        long newRemaining = 0;
        boolean inHunk = false;

        for (String line : section.split("\\r?\\n", -1)) {
            var header = DiffParsingUtils.HUNK_HEADER.matcher(line);
            if (header.find()) {
                if (inHunk && (oldRemaining != 0 || newRemaining != 0)) {
                    throw new IOException("Provider exact diff contains a truncated hunk");
                }
                try {
                    oldRemaining = header.group(2) == null
                            ? 1 : Long.parseLong(header.group(2));
                    newRemaining = header.group(4) == null
                            ? 1 : Long.parseLong(header.group(4));
                } catch (NumberFormatException invalidCount) {
                    throw new IOException("Provider exact diff contains an invalid hunk count", invalidCount);
                }
                inHunk = true;
                continue;
            }
            if (!inHunk) continue;
            if (line.startsWith("\\ No newline at end of file")) continue;
            if (oldRemaining == 0 && newRemaining == 0) {
                if (!line.isEmpty() && (line.charAt(0) == '+'
                        || line.charAt(0) == '-' || line.charAt(0) == ' ')) {
                    throw new IOException("Provider exact diff contains content outside a hunk");
                }
                inHunk = false;
                continue;
            }
            if (line.isEmpty()) {
                throw new IOException("Provider exact diff contains a truncated hunk");
            }
            switch (line.charAt(0)) {
                case '+' -> {
                    if (newRemaining == 0) {
                        throw new IOException("Provider exact diff exceeds its new-line hunk count");
                    }
                    newRemaining--;
                    additions++;
                }
                case '-' -> {
                    if (oldRemaining == 0) {
                        throw new IOException("Provider exact diff exceeds its old-line hunk count");
                    }
                    oldRemaining--;
                    deletions++;
                }
                case ' ' -> {
                    if (oldRemaining == 0 || newRemaining == 0) {
                        throw new IOException("Provider exact diff exceeds its context hunk count");
                    }
                    oldRemaining--;
                    newRemaining--;
                }
                default -> throw new IOException(
                        "Provider exact diff contains malformed hunk content");
            }
        }
        if (inHunk && (oldRemaining != 0 || newRemaining != 0)) {
            throw new IOException("Provider exact diff contains a truncated hunk");
        }
        return new DiffLineCounts(additions, deletions);
    }

    private record DiffLineCounts(long additions, long deletions) {}
}

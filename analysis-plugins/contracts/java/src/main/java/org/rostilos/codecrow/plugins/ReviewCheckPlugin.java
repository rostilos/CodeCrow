package org.rostilos.codecrow.plugins;

import java.io.IOException;
import java.util.List;

/** Optional deterministic PR checks. The host supplies only tenant-bound, pinned source reads. */
public interface ReviewCheckPlugin extends CodeCrowPlugin {
    default boolean appliesTo(List<String> changedPaths) { return true; }

    /** Stable identity within this check, independent of narrative and source-line changes. */
    default String findingKey(Finding finding) { return finding.file() + "\0" + finding.title(); }

    Result check(Context context) throws IOException, InterruptedException;

    @FunctionalInterface
    interface SourceReader {
        /** Null means absent; unavailable reads must throw instead. */
        String read(String revision, String path) throws IOException;
    }

    record Context(String baseRevision, String headRevision, List<String> changedPaths,
                   SourceReader source) {}

    record Finding(String file, int line, String severity, String title, String reason,
                   String suggestedFix, String codeSnippet) {}

    record Result(List<Finding> findings, List<String> diagnostics, boolean complete) {
        public Result {
            findings = List.copyOf(findings);
            diagnostics = List.copyOf(diagnostics);
        }
    }
}

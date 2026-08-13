package org.rostilos.codecrow.vcsclient.model;

import java.util.LinkedHashSet;
import java.util.List;

/**
 * Provider-neutral inventory of paths changed between a pull request's base
 * and current head.
 *
 * <p>The manifest is intentionally separate from unified patch text. A patch
 * can omit binary, oversized, or provider-truncated entries and therefore is
 * never proof that the path inventory is complete.</p>
 */
public record VcsPullRequestChangeManifest(
        List<Change> changes,
        Completeness completeness,
        String receipt) {

    public VcsPullRequestChangeManifest {
        changes = changes != null ? List.copyOf(changes) : List.of();
        completeness = completeness != null ? completeness : Completeness.UNAVAILABLE;
        receipt = receipt != null ? receipt : "";
    }

    public static VcsPullRequestChangeManifest unavailable(String receipt) {
        return new VcsPullRequestChangeManifest(List.of(), Completeness.UNAVAILABLE, receipt);
    }

    public static VcsPullRequestChangeManifest incomplete(
            List<Change> changes,
            String receipt) {
        return new VcsPullRequestChangeManifest(changes, Completeness.INCOMPLETE, receipt);
    }

    public boolean isComplete() {
        return completeness == Completeness.COMPLETE;
    }

    /** Current-head paths which may have source content. */
    public List<String> currentPaths() {
        LinkedHashSet<String> paths = new LinkedHashSet<>();
        for (Change change : changes) {
            if (change != null
                    && change.kind() != ChangeKind.DELETED
                    && !change.path().isBlank()) {
                paths.add(change.path());
            }
        }
        return List.copyOf(paths);
    }

    /** Paths which must be removed from an older overlay (deletes and rename sources). */
    public List<String> removedPaths() {
        LinkedHashSet<String> paths = new LinkedHashSet<>();
        for (Change change : changes) {
            if (change == null) {
                continue;
            }
            if (change.kind() == ChangeKind.DELETED && !change.path().isBlank()) {
                paths.add(change.path());
            }
            if (change.kind() == ChangeKind.RENAMED && !change.previousPath().isBlank()) {
                paths.add(change.previousPath());
            }
        }
        return List.copyOf(paths);
    }

    public enum Completeness {
        COMPLETE,
        INCOMPLETE,
        UNAVAILABLE
    }

    public enum ChangeKind {
        ADDED,
        MODIFIED,
        DELETED,
        RENAMED,
        COPIED,
        UNKNOWN
    }

    public record Change(String path, String previousPath, ChangeKind kind) {
        public Change {
            path = path != null ? path : "";
            previousPath = previousPath != null ? previousPath : "";
            kind = kind != null ? kind : ChangeKind.UNKNOWN;
        }
    }
}

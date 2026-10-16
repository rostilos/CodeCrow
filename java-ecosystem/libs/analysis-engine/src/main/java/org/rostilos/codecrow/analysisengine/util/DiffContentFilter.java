package org.rostilos.codecrow.analysisengine.util;

/**
 * Compatibility identity shim for callers compiled against the former diff
 * filter. Large delta files must reach the semantic inference packer intact;
 * replacing them with a placeholder made incremental QA conclusions incomplete.
 *
 * @deprecated pass the raw diff directly instead
 */
@Deprecated(forRemoval = true)
public final class DiffContentFilter {

    public DiffContentFilter() {
    }

    public DiffContentFilter(int ignoredSizeThresholdBytes) {
    }

    /** Return the complete supplied diff without size-based filtering. */
    public String filterDiff(String rawDiff) {
        return rawDiff;
    }
}

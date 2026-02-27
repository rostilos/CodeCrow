package org.rostilos.codecrow.core.util.tracking;

/**
 * Confidence level indicating which pass of the {@link IssueTracker} matched an issue.
 * Higher confidence means more attributes matched between old and new issue.
 */
public enum TrackingConfidence {

    /**
     * Pass 1: fingerprint + line + lineHash all matched.
     * The issue is in the exact same place with the exact same content.
     */
    EXACT,

    /**
     * Pass 2: fingerprint + lineHash matched, but line number differs.
     * The source line content is unchanged but was shifted by surrounding edits.
     */
    SHIFTED,

    /**
     * Pass 3: fingerprint + line matched, but lineHash differs.
     * The code at the original line was modified, but the issue pattern persists at the same location.
     */
    EDITED,

    /**
     * Pass 4: only fingerprint matched (category + content anchor + normalized title).
     * Both line number and content changed, but the same logical issue class persists in the same file.
     */
    WEAK,

    /**
     * The issue was not matched by any tracker pass — it is either new or auto-resolved.
     */
    NONE
}

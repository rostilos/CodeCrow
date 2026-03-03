package org.rostilos.codecrow.commitgraph.dag;

import java.util.Collections;
import java.util.List;

/**
 * Result of the commit-range resolution for a branch analysis.
 * <p>
 * Replaces the old DAG-based approach. Instead of walking a graph in the DB,
 * this simply carries the list of commits needing analysis and whether to skip.
 *
 * @param unanalyzedCommits commit hashes that need analysis (oldest first)
 * @param diffBase          the commit hash to diff against (lastKnownHeadCommit),
 *                          or null for first analysis
 * @param skipAnalysis      true if all commits are already analyzed
 */
public record CommitRangeContext(
        List<String> unanalyzedCommits,
        String diffBase,
        boolean skipAnalysis
) {
    public static CommitRangeContext skip() {
        return new CommitRangeContext(Collections.emptyList(), null, true);
    }

    public static CommitRangeContext firstAnalysis(String headCommit) {
        return new CommitRangeContext(List.of(headCommit), null, false);
    }

    public String getDiffBase() {
        return diffBase;
    }

    public List<String> getUnanalyzedCommits() {
        return unanalyzedCommits;
    }

    public boolean getSkipAnalysis() {
        return skipAnalysis;
    }
}

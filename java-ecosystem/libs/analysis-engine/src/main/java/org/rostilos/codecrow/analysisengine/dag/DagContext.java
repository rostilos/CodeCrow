package org.rostilos.codecrow.analysisengine.dag;

import java.util.List;

public record DagContext(
        List<String> unanalyzedCommits,
        String diffBase,
        boolean skipAnalysis
) {
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
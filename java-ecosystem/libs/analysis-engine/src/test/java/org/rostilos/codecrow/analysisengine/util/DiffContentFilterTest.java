package org.rostilos.codecrow.analysisengine.util;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("DiffContentFilter compatibility shim")
class DiffContentFilterTest {

    @Test
    @DisplayName("preserves a large multi-file Unicode diff byte for byte")
    void preservesLargeDiffWithoutFiltering() {
        String diff = "diff --git a/src/Large.java b/src/Large.java\n"
                + "--- a/src/Large.java\n"
                + "+++ b/src/Large.java\n"
                + ("+зміна 🧪\n".repeat(8_000))
                + "DIFF-END\n";

        assertThat(new DiffContentFilter(100).filterDiff(diff)).isSameAs(diff);
        assertThat(new DiffContentFilter().filterDiff(diff)).endsWith("DIFF-END\n");
    }

    @Test
    @DisplayName("preserves null and empty inputs")
    void preservesEmptyInputs() {
        DiffContentFilter filter = new DiffContentFilter();

        assertThat(filter.filterDiff(null)).isNull();
        assertThat(filter.filterDiff("")).isEmpty();
    }
}

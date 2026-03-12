package org.rostilos.codecrow.commitgraph.dag;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class CommitRangeContextTest {

    @Test
    void skip_shouldReturnEmptyListAndSkipTrue() {
        CommitRangeContext ctx = CommitRangeContext.skip();

        assertThat(ctx.unanalyzedCommits()).isEmpty();
        assertThat(ctx.diffBase()).isNull();
        assertThat(ctx.skipAnalysis()).isTrue();
    }

    @Test
    void skip_gettersMatchAccessors() {
        CommitRangeContext ctx = CommitRangeContext.skip();

        assertThat(ctx.getUnanalyzedCommits()).isEmpty();
        assertThat(ctx.getDiffBase()).isNull();
        assertThat(ctx.getSkipAnalysis()).isTrue();
    }

    @Test
    void firstAnalysis_shouldContainSingleCommitAndNullDiffBase() {
        CommitRangeContext ctx = CommitRangeContext.firstAnalysis("abc123");

        assertThat(ctx.unanalyzedCommits()).containsExactly("abc123");
        assertThat(ctx.diffBase()).isNull();
        assertThat(ctx.skipAnalysis()).isFalse();
    }

    @Test
    void firstAnalysis_gettersMatchAccessors() {
        CommitRangeContext ctx = CommitRangeContext.firstAnalysis("abc123");

        assertThat(ctx.getUnanalyzedCommits()).containsExactly("abc123");
        assertThat(ctx.getDiffBase()).isNull();
        assertThat(ctx.getSkipAnalysis()).isFalse();
    }

    @Test
    void constructor_shouldPreserveAllFields() {
        List<String> commits = List.of("aaa", "bbb", "ccc");
        CommitRangeContext ctx = new CommitRangeContext(commits, "base-hash", false);

        assertThat(ctx.unanalyzedCommits()).containsExactly("aaa", "bbb", "ccc");
        assertThat(ctx.diffBase()).isEqualTo("base-hash");
        assertThat(ctx.skipAnalysis()).isFalse();
    }

    @Test
    void constructor_withSkipTrue_shouldBeSkipped() {
        CommitRangeContext ctx = new CommitRangeContext(List.of(), "some-base", true);

        assertThat(ctx.skipAnalysis()).isTrue();
        assertThat(ctx.diffBase()).isEqualTo("some-base");
    }

    @Test
    void record_equalityByContent() {
        CommitRangeContext a = new CommitRangeContext(List.of("x"), "b", false);
        CommitRangeContext b = new CommitRangeContext(List.of("x"), "b", false);

        assertThat(a).isEqualTo(b);
        assertThat(a.hashCode()).isEqualTo(b.hashCode());
    }

    @Test
    void record_notEqualWhenDifferent() {
        CommitRangeContext a = new CommitRangeContext(List.of("x"), "b", false);
        CommitRangeContext b = new CommitRangeContext(List.of("y"), "b", false);

        assertThat(a).isNotEqualTo(b);
    }

    @Test
    void toString_shouldContainFieldValues() {
        CommitRangeContext ctx = CommitRangeContext.firstAnalysis("abc");

        assertThat(ctx.toString()).contains("abc");
    }
}

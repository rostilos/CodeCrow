package org.rostilos.codecrow.pipelineagent.bitbucket.service;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.AnalysisSummary;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class BitbucketInlineCommentFormatterTest {
    private static final String MARKER = "[codecrow-inline-issue]: #";

    private final BitbucketInlineCommentFormatter formatter =
            new BitbucketInlineCommentFormatter();

    @Test
    void formatsAnchoredIssueForBitbucketMarkdown() {
        AnalysisSummary.IssueSummary issue = issue(
                "/src/App.java",
                42,
                "Use a bounded cache",
                "The cache can grow without limit.",
                "Replace it with a bounded implementation.\nPreserve expiry behavior.",
                "--- a/src/App.java\n+++ b/src/App.java\n@@ -42 +42 @@\n-old\n+new"
        );

        List<BitbucketInlineCommentFormatter.InlineComment> comments =
                formatter.formatComments(List.of(issue), MARKER);

        assertThat(comments).hasSize(1);
        assertThat(comments.get(0).path()).isEqualTo("src/App.java");
        assertThat(comments.get(0).line()).isEqualTo(42);
        assertThat(comments.get(0).body())
                .contains("🟡 **MEDIUM** | Error Handling")
                .contains("**Use a bounded cache**")
                .contains("The cache can grow without limit.")
                .contains("**Suggested fix**")
                .contains("> Replace it with a bounded implementation.\n> Preserve expiry behavior.")
                .contains("**Suggested code change**")
                .contains("```diff")
                .contains("[View issue in CodeCrow](https://codecrow.example/issues/1)")
                .endsWith(MARKER);
        assertThat(comments.get(0).body())
                .doesNotContain("<details>")
                .doesNotContain("<!-- codecrow-inline-issue -->");
    }

    @Test
    void skipsIssuesWithoutAConfidentInlineAnchor() {
        AnalysisSummary.IssueSummary noPath =
                issue(null, 10, "No path", "Reason", null, null);
        AnalysisSummary.IssueSummary noLine =
                issue("src/App.java", null, "No line", "Reason", null, null);
        AnalysisSummary.IssueSummary syntheticLineOne =
                issue("src/App.java", 1, "Synthetic", "Reason", null, null);

        assertThat(formatter.formatComments(
                List.of(noPath, noLine, syntheticLineOne), MARKER)).isEmpty();
    }

    @Test
    void keepsRealLineOneAndDoesNotImposeAnArbitraryCommentCap() {
        AnalysisSummary.IssueSummary lineOne = new AnalysisSummary.IssueSummary(
                IssueSeverity.LOW,
                "CODE_QUALITY",
                "src/App.java",
                1,
                "Package declaration",
                "The declaration is inconsistent.",
                null,
                null,
                null,
                1L,
                "package example;"
        );

        assertThat(formatter.formatComments(
                java.util.Collections.nCopies(25, lineOne), MARKER)).hasSize(25);
    }

    private AnalysisSummary.IssueSummary issue(
            String path,
            Integer line,
            String title,
            String reason,
            String fix,
            String diff
    ) {
        return new AnalysisSummary.IssueSummary(
                IssueSeverity.MEDIUM,
                "ERROR_HANDLING",
                path,
                line,
                title,
                reason,
                fix,
                diff,
                "https://codecrow.example/issues/1",
                1L
        );
    }
}

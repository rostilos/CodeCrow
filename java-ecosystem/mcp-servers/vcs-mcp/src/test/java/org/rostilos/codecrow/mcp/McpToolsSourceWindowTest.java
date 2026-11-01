package org.rostilos.codecrow.mcp;

import org.junit.jupiter.api.Test;

import java.util.Map;
import java.util.stream.Collectors;
import java.util.stream.IntStream;

import static org.assertj.core.api.Assertions.assertThat;

class McpToolsSourceWindowTest {

    @Test
    void returnsAnchorCentredLinesWithCoverageMetadata() {
        String content = numberedLines(1_000);

        Map<String, Object> result = McpTools.sourceWindow(content, 420, 580);

        assertThat(result.get("startLine")).isEqualTo(420);
        assertThat(result.get("endLine")).isEqualTo(580);
        assertThat(result.get("totalLines")).isEqualTo(1_000);
        assertThat(result.get("completeFile")).isEqualTo(false);
        assertThat((String) result.get("fileContent"))
                .startsWith("line-420\n")
                .endsWith("line-580");
    }

    @Test
    void boundsAnOverwideRequestAtWholeLineBoundaries() {
        Map<String, Object> result = McpTools.sourceWindow(
                numberedLines(1_000),
                1,
                1_000
        );

        assertThat(result.get("startLine")).isEqualTo(1);
        assertThat(result.get("endLine")).isEqualTo(401);
        assertThat(result.get("completeFile")).isEqualTo(false);
        assertThat(((String) result.get("fileContent")).lines()).hasSize(401);
    }

    @Test
    void marksACompleteSmallFile() {
        Map<String, Object> result = McpTools.sourceWindow(
                "first\nsecond\nthird",
                1,
                3
        );

        assertThat(result.get("fileContent")).isEqualTo("first\nsecond\nthird");
        assertThat(result.get("completeFile")).isEqualTo(true);
    }

    @Test
    void returnsAnExplicitTailWindowWhenTheRequestedStartIsBeyondEof() {
        Map<String, Object> result = McpTools.sourceWindow(numberedLines(509), 530, 610);

        assertThat(result)
                .doesNotContainKey("error")
                .containsEntry("requestedStartLine", 530)
                .containsEntry("requestedEndLine", 610)
                .containsEntry("rangeAdjustment", "requested_start_beyond_eof")
                .containsEntry("startLine", 429)
                .containsEntry("endLine", 509)
                .containsEntry("totalLines", 509)
                .containsEntry("completeFile", false)
                .containsEntry("fileContent", numberedLines(509).substring(numberedLines(428).length() + 1));
    }

    @Test
    void boundsAnOutOfFileWindowAndRetainsUnavailableContentErrors() {
        Map<String, Object> bounded = McpTools.sourceWindow(numberedLines(509), 530, Integer.MAX_VALUE);
        assertThat(bounded).containsEntry("startLine", 109).containsEntry("endLine", 509);
        assertThat(((String) bounded.get("fileContent")).lines()).hasSize(401);
        assertThat(McpTools.sourceWindow(null, 530, 610)).containsKey("error");
    }

    @Test
    void boundsTailWindowsForSmallEmptyAndUnspecifiedEndRequests() {
        assertThat(McpTools.sourceWindow("one\ntwo", 3, 5))
                .containsEntry("fileContent", "one\ntwo")
                .containsEntry("startLine", 1)
                .containsEntry("endLine", 2)
                .containsEntry("completeFile", true);
        assertThat(McpTools.sourceWindow("one\ntwo", Integer.MAX_VALUE, null))
                .containsEntry("fileContent", "two")
                .containsEntry("startLine", 2)
                .containsEntry("endLine", 2);
        assertThat(McpTools.sourceWindow("", 530, 610))
                .containsEntry("fileContent", "")
                .containsEntry("totalLines", 1)
                .containsEntry("completeFile", true);
    }

    @Test
    void clipsAnOverlappingWindowAtEofWithoutMovingItsStart() {
        assertThat(McpTools.sourceWindow("one\ntwo\nthree", 2, 20))
                .containsEntry("startLine", 2)
                .containsEntry("endLine", 3)
                .containsEntry("fileContent", "two\nthree")
                .doesNotContainKey("rangeAdjustment");
    }

    private String numberedLines(int count) {
        return IntStream.rangeClosed(1, count)
                .mapToObj(index -> "line-" + index)
                .collect(Collectors.joining("\n"));
    }
}

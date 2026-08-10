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
    void rejectsAnAnchorOutsideTheFile() {
        Map<String, Object> result = McpTools.sourceWindow("one\ntwo", 3, 5);

        assertThat(result).containsKey("error");
        assertThat(result.get("totalLines")).isEqualTo(2);
    }

    private String numberedLines(int count) {
        return IntStream.rangeClosed(1, count)
                .mapToObj(index -> "line-" + index)
                .collect(Collectors.joining("\n"));
    }
}

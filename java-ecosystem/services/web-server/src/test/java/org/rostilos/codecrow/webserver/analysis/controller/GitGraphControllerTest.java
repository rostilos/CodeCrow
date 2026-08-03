package org.rostilos.codecrow.webserver.analysis.controller;

import org.junit.jupiter.api.Test;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class GitGraphControllerTest {

    @Test
    void ordersMergedHistoriesAsAChildBeforeParentDag() {
        List<Map<String, Object>> providerOrder = new ArrayList<>(List.of(
                commit("feature", "2026-01-03T10:00:00Z", "base"),
                commit("base", "2026-01-01T10:00:00Z"),
                commit("merge", "2026-01-04T10:00:00Z", "main", "feature"),
                commit("main", "2026-01-02T10:00:00Z", "base")));

        List<Map<String, Object>> ordered =
                GitGraphController.topologicallyOrderCommits(providerOrder);

        List<String> hashes = ordered.stream()
                .map(commit -> (String) commit.get("hash"))
                .toList();
        assertThat(hashes).containsExactly("merge", "feature", "main", "base");
        assertThat(hashes.indexOf("merge")).isLessThan(hashes.indexOf("feature"));
        assertThat(hashes.indexOf("merge")).isLessThan(hashes.indexOf("main"));
        assertThat(hashes.indexOf("feature")).isLessThan(hashes.indexOf("base"));
        assertThat(hashes.indexOf("main")).isLessThan(hashes.indexOf("base"));
    }

    private static Map<String, Object> commit(
            String hash,
            String timestamp,
            String... parents) {
        Map<String, Object> commit = new LinkedHashMap<>();
        commit.put("hash", hash);
        commit.put("timestamp", OffsetDateTime.parse(timestamp));
        commit.put("parents", List.of(parents));
        return commit;
    }
}

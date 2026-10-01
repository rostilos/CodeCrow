package org.rostilos.codecrow.core.model.project.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class RagConfigTest {

    @Test
    void defaultConstructorDisablesRag() {
        RagConfig config = new RagConfig();

        assertThat(config.enabled()).isFalse();
        assertThat(config.branch()).isNull();
        assertThat(config.includePatterns()).isNull();
        assertThat(config.excludePatterns()).isNull();
    }

    @Test
    void constructorsPreserveCurrentIndexInputs() {
        RagConfig config = new RagConfig(
                true,
                "main",
                List.of("src/**"),
                List.of("vendor/**"));

        assertThat(config.enabled()).isTrue();
        assertThat(config.branch()).isEqualTo("main");
        assertThat(config.includePatterns()).containsExactly("src/**");
        assertThat(config.excludePatterns()).containsExactly("vendor/**");
        assertThat(new RagConfig(true, "develop", List.of("*.tmp"))
                .excludePatterns()).containsExactly("*.tmp");
    }

    @Test
    void equalityUsesOnlyCurrentContract() {
        RagConfig first = new RagConfig(
                true, "main", List.of("src/**"), List.of("vendor/**"));
        RagConfig second = new RagConfig(
                true, "main", List.of("src/**"), List.of("vendor/**"));

        assertThat(first).isEqualTo(second);
        assertThat(first.hashCode()).isEqualTo(second.hashCode());
        assertThat(first).isNotEqualTo(new RagConfig(false, "main"));
    }

    @Test
    void oldBranchPolicyFieldsAreIgnoredDuringDeserialization() throws Exception {
        ObjectMapper mapper = new ObjectMapper();
        RagConfig restored = mapper.readValue("""
                {
                  "enabled": true,
                  "branch": "main",
                  "includePatterns": ["src/**"],
                  "excludePatterns": ["vendor/**"],
                  "multiBranchEnabled": true,
                  "indexedBranches": ["develop"],
                  "transientBranchIndexesEnabled": true,
                  "branchRetentionDays": 30
                }
                """, RagConfig.class);

        assertThat(restored).isEqualTo(new RagConfig(
                true, "main", List.of("src/**"), List.of("vendor/**")));
    }

    @Test
    void serializationContainsOnlyCurrentContract() throws Exception {
        ObjectMapper mapper = new ObjectMapper();
        String json = mapper.writeValueAsString(new RagConfig(
                true, "main", List.of("src/**"), List.of("vendor/**")));

        assertThat(json)
                .contains("\"includePatterns\"")
                .contains("\"excludePatterns\"")
                .doesNotContain("multiBranchEnabled")
                .doesNotContain("indexedBranches")
                .doesNotContain("transientBranchIndexesEnabled")
                .doesNotContain("branchRetentionDays");
    }
}

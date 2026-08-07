package org.rostilos.codecrow.core.model.project.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class RagConfigTest {

    @Test
    void shouldCreateWithDefaultConstructor() {
        RagConfig config = new RagConfig();
        
        assertThat(config.enabled()).isFalse();
        assertThat(config.branch()).isNull();
        assertThat(config.excludePatterns()).isNull();
        assertThat(config.isMultiBranchEnabled()).isFalse();
        assertThat(config.branchRetentionDays()).isEqualTo(RagConfig.DEFAULT_BRANCH_RETENTION_DAYS);
    }

    @Test
    void shouldCreateWithEnabledOnly() {
        RagConfig config = new RagConfig(true);
        
        assertThat(config.enabled()).isTrue();
        assertThat(config.branch()).isNull();
        assertThat(config.excludePatterns()).isNull();
        assertThat(config.isMultiBranchEnabled()).isFalse();
    }

    @Test
    void shouldCreateWithEnabledAndBranch() {
        RagConfig config = new RagConfig(true, "main");
        
        assertThat(config.enabled()).isTrue();
        assertThat(config.branch()).isEqualTo("main");
        assertThat(config.excludePatterns()).isNull();
        assertThat(config.isMultiBranchEnabled()).isFalse();
    }

    @Test
    void shouldCreateWithEnabledBranchAndExcludePatterns() {
        List<String> patterns = List.of("vendor/*", "*.generated.ts");
        RagConfig config = new RagConfig(true, "develop", patterns);
        
        assertThat(config.enabled()).isTrue();
        assertThat(config.branch()).isEqualTo("develop");
        assertThat(config.excludePatterns()).isEqualTo(patterns);
        assertThat(config.isMultiBranchEnabled()).isFalse();
    }

    @Test
    void shouldCreateWithAllParameters() {
        List<String> patterns = List.of("app/code/**");
        RagConfig config = new RagConfig(true, "main", null, patterns, true, 60);
        
        assertThat(config.enabled()).isTrue();
        assertThat(config.branch()).isEqualTo("main");
        assertThat(config.excludePatterns()).containsExactly("app/code/**");
        assertThat(config.isMultiBranchEnabled()).isTrue();
        assertThat(config.branchRetentionDays()).isEqualTo(60);
    }

    @Test
    void isMultiBranchEnabled_shouldReturnTrueWhenMultiBranchEnabledIsTrue() {
        RagConfig config = new RagConfig(true, "main", null, null, true, 90);
        
        assertThat(config.isMultiBranchEnabled()).isTrue();
    }

    @Test
    void isMultiBranchEnabled_shouldReturnFalseWhenMultiBranchEnabledIsFalse() {
        RagConfig config = new RagConfig(true, "main", null, null, false, 90);
        
        assertThat(config.isMultiBranchEnabled()).isFalse();
    }

    @Test
    void isMultiBranchEnabled_shouldReturnFalseWhenMultiBranchEnabledIsNull() {
        RagConfig config = new RagConfig(true, "main", null, null, null, 90);
        
        assertThat(config.isMultiBranchEnabled()).isFalse();
    }

    @Test
    void shouldSupportEquality() {
        RagConfig config1 = new RagConfig(true, "main", null, List.of("vendor/*"), true, 90);
        RagConfig config2 = new RagConfig(true, "main", null, List.of("vendor/*"), true, 90);
        
        assertThat(config1).isEqualTo(config2);
        assertThat(config1.hashCode()).isEqualTo(config2.hashCode());
    }

    @Test
    void shouldSupportInequality() {
        RagConfig config1 = new RagConfig(true, "main", null, null, false, 90);
        RagConfig config2 = new RagConfig(false, "main", null, null, false, 90);
        
        assertThat(config1).isNotEqualTo(config2);
    }

    @Test
    void shouldHaveDefaultBranchRetentionDaysConstant() {
        assertThat(RagConfig.DEFAULT_BRANCH_RETENTION_DAYS).isEqualTo(90);
    }

    @Test
    void explicitIndexedBranchesShouldOverrideLegacyPushPatterns() {
        RagConfig config = new RagConfig(
                true,
                "master",
                null,
                null,
                true,
                30,
                List.of(" develop ", "support/1.x", "develop", " "),
                true);

        assertThat(config.getEffectiveIndexedBranches()).containsExactly("develop", "support/1.x");
        assertThat(config.shouldHaveBranchIndex("develop", List.of("feature/**"))).isTrue();
        assertThat(config.shouldHaveBranchIndex("feature/one", List.of("feature/**"))).isFalse();
        assertThat(config.isTransientBranchIndexesEnabled()).isTrue();
    }

    @Test
    void legacyConfigurationShouldContinueUsingPushPatterns() {
        RagConfig config = new RagConfig(true, "master", null, null, true, 30);

        assertThat(config.hasExplicitIndexedBranches()).isFalse();
        assertThat(config.shouldHaveBranchIndex("develop", List.of("develop", "support/**"))).isTrue();
        assertThat(config.shouldHaveBranchIndex("support/1.x", List.of("develop", "support/**"))).isTrue();
        assertThat(config.shouldHaveBranchIndex("feature/one", List.of("develop", "support/**"))).isFalse();
        assertThat(config.isTransientBranchIndexesEnabled()).isFalse();
    }

    @Test
    void transientIndexesRequireMultiBranchOwnership() {
        RagConfig config = new RagConfig(
                true, "master", null, null, false, 30, List.of("develop"), true);

        assertThat(config.isTransientBranchIndexesEnabled()).isFalse();
        assertThat(config.shouldHaveBranchIndex("develop", List.of("develop"))).isFalse();
    }

    @Test
    void serializesOnlyPersistedFieldsAndRoundTripsRetainedBranches() throws Exception {
        ProjectConfig projectConfig = new ProjectConfig();
        projectConfig.setRagConfig(new RagConfig(
                true, "master", null, null, true, 30,
                List.of("develop", "release/1.x"), true));

        ObjectMapper mapper = new ObjectMapper();
        String json = mapper.writeValueAsString(projectConfig);

        assertThat(json)
                .doesNotContain("effectiveIndexedBranches")
                .doesNotContain("effectiveBranchRetentionDays")
                .doesNotContain("isMultiBranchEnabled")
                .doesNotContain("isTransientBranchIndexesEnabled");

        ProjectConfig restored = mapper.readValue(json, ProjectConfig.class);
        assertThat(restored.ragConfig().indexedBranches())
                .containsExactly("develop", "release/1.x");
        assertThat(restored.ragConfig().getEffectiveIndexedBranches())
                .containsExactly("develop", "release/1.x");
        assertThat(restored.ragConfig().isTransientBranchIndexesEnabled()).isTrue();
    }
}

package org.rostilos.codecrow.core.model.project.config;

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
        assertThat(config.isDeltaEnabled()).isFalse();
        assertThat(config.deltaRetentionDays()).isEqualTo(RagConfig.DEFAULT_DELTA_RETENTION_DAYS);
    }

    @Test
    void shouldCreateWithEnabledOnly() {
        RagConfig config = new RagConfig(true);
        
        assertThat(config.enabled()).isTrue();
        assertThat(config.branch()).isNull();
        assertThat(config.excludePatterns()).isNull();
        assertThat(config.isDeltaEnabled()).isFalse();
    }

    @Test
    void shouldCreateWithEnabledAndBranch() {
        RagConfig config = new RagConfig(true, "main");
        
        assertThat(config.enabled()).isTrue();
        assertThat(config.branch()).isEqualTo("main");
        assertThat(config.excludePatterns()).isNull();
        assertThat(config.isDeltaEnabled()).isFalse();
    }

    @Test
    void shouldCreateWithEnabledBranchAndExcludePatterns() {
        List<String> patterns = List.of("vendor/*", "*.generated.ts");
        RagConfig config = new RagConfig(true, "develop", patterns);
        
        assertThat(config.enabled()).isTrue();
        assertThat(config.branch()).isEqualTo("develop");
        assertThat(config.excludePatterns()).isEqualTo(patterns);
        assertThat(config.isDeltaEnabled()).isFalse();
    }

    @Test
    void shouldCreateWithAllParameters() {
        List<String> patterns = List.of("app/code/**");
        RagConfig config = new RagConfig(true, "main", patterns, true, 60);
        
        assertThat(config.enabled()).isTrue();
        assertThat(config.branch()).isEqualTo("main");
        assertThat(config.excludePatterns()).containsExactly("app/code/**");
        assertThat(config.isDeltaEnabled()).isTrue();
        assertThat(config.deltaRetentionDays()).isEqualTo(60);
    }

    @Test
    void isDeltaEnabled_shouldReturnTrueWhenDeltaEnabledIsTrue() {
        RagConfig config = new RagConfig(true, "main", null, true, 90);
        
        assertThat(config.isDeltaEnabled()).isTrue();
    }

    @Test
    void isDeltaEnabled_shouldReturnFalseWhenDeltaEnabledIsFalse() {
        RagConfig config = new RagConfig(true, "main", null, false, 90);
        
        assertThat(config.isDeltaEnabled()).isFalse();
    }

    @Test
    void isDeltaEnabled_shouldReturnFalseWhenDeltaEnabledIsNull() {
        RagConfig config = new RagConfig(true, "main", null, null, 90);
        
        assertThat(config.isDeltaEnabled()).isFalse();
    }

    @Test
    void shouldSupportEquality() {
        RagConfig config1 = new RagConfig(true, "main", List.of("vendor/*"), true, 90);
        RagConfig config2 = new RagConfig(true, "main", List.of("vendor/*"), true, 90);
        
        assertThat(config1).isEqualTo(config2);
        assertThat(config1.hashCode()).isEqualTo(config2.hashCode());
    }

    @Test
    void shouldSupportInequality() {
        RagConfig config1 = new RagConfig(true, "main", null, false, 90);
        RagConfig config2 = new RagConfig(false, "main", null, false, 90);
        
        assertThat(config1).isNotEqualTo(config2);
    }

    @Test
    void shouldHaveDefaultDeltaRetentionDaysConstant() {
        assertThat(RagConfig.DEFAULT_DELTA_RETENTION_DAYS).isEqualTo(90);
    }
}

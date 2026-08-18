package org.rostilos.codecrow.core.model.project.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class AnalysisProfileConfigTest {
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void normalizesManualTypeAndNestedRoot() {
        var profile = new AnalysisProfileConfig(" Magento ", "magento/src/etc");
        assertThat(profile.projectType()).isEqualTo("magento");
        assertThat(profile.sourceRoot()).isEqualTo("magento/src/etc");
        assertThat(profile.isAutomatic()).isFalse();
    }

    @Test
    void treatsAutoAndRepositoryRootAsUnspecified() {
        var profile = new AnalysisProfileConfig("auto", ".");
        assertThat(profile.projectType()).isNull();
        assertThat(profile.sourceRoot()).isNull();
    }

    @Test
    void rejectsEscapingSourceRoot() {
        assertThatThrownBy(() -> new AnalysisProfileConfig("magento", "../shop"))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void jsonRoundTripPersistsOnlyConfiguredFields() throws Exception {
        var profile = new AnalysisProfileConfig(null, "magento/src");

        String json = objectMapper.writeValueAsString(profile);

        assertThat(json).doesNotContain("automatic");
        assertThat(objectMapper.readValue(json, AnalysisProfileConfig.class)).isEqualTo(profile);
    }
}

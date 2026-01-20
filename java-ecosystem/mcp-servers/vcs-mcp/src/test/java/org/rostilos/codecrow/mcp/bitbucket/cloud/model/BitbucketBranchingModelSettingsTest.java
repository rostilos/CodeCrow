package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.mcp.bitbucket.cloud.model.BitbucketBranchingModelSettings.*;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("BitbucketBranchingModelSettings")
class BitbucketBranchingModelSettingsTest {

    @Test
    @DisplayName("should create record with all fields")
    void shouldCreateRecordWithAllFields() {
        DevelopmentDto development = new DevelopmentDto("develop", true, true);
        ProductionDto production = new ProductionDto("main", true, true, true);
        List<BranchTypeDto> branchTypes = List.of(
                new BranchTypeDto("feature", "feature/", true),
                new BranchTypeDto("bugfix", "bugfix/", true),
                new BranchTypeDto("release", "release/", false)
        );

        BitbucketBranchingModelSettings settings = new BitbucketBranchingModelSettings(
                "branching_model_settings",
                development,
                production,
                branchTypes,
                Map.of()
        );

        assertThat(settings.type()).isEqualTo("branching_model_settings");
        assertThat(settings.development()).isEqualTo(development);
        assertThat(settings.production()).isEqualTo(production);
        assertThat(settings.branchTypes()).hasSize(3);
    }

    @Nested
    @DisplayName("DevelopmentDto")
    class DevelopmentDtoTests {

        @Test
        @DisplayName("should create with all fields")
        void shouldCreateWithAllFields() {
            DevelopmentDto dto = new DevelopmentDto("develop", true, true);

            assertThat(dto.name()).isEqualTo("develop");
            assertThat(dto.useMainbranch()).isTrue();
            assertThat(dto.isValid()).isTrue();
        }

        @Test
        @DisplayName("should handle null isValid")
        void shouldHandleNullIsValid() {
            DevelopmentDto dto = new DevelopmentDto("develop", false, null);

            assertThat(dto.isValid()).isNull();
        }
    }

    @Nested
    @DisplayName("ProductionDto")
    class ProductionDtoTests {

        @Test
        @DisplayName("should create with all fields")
        void shouldCreateWithAllFields() {
            ProductionDto dto = new ProductionDto("main", true, true, true);

            assertThat(dto.name()).isEqualTo("main");
            assertThat(dto.useMainbranch()).isTrue();
            assertThat(dto.enabled()).isTrue();
            assertThat(dto.isValid()).isTrue();
        }

        @Test
        @DisplayName("should handle disabled production")
        void shouldHandleDisabledProduction() {
            ProductionDto dto = new ProductionDto("production", false, false, false);

            assertThat(dto.enabled()).isFalse();
            assertThat(dto.isValid()).isFalse();
        }
    }

    @Nested
    @DisplayName("BranchTypeDto")
    class BranchTypeDtoTests {

        @Test
        @DisplayName("should create with enabled flag")
        void shouldCreateWithEnabledFlag() {
            BranchTypeDto enabled = new BranchTypeDto("feature", "feature/", true);
            BranchTypeDto disabled = new BranchTypeDto("hotfix", "hotfix/", false);

            assertThat(enabled.enabled()).isTrue();
            assertThat(disabled.enabled()).isFalse();
        }
    }
}

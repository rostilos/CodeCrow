package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.mcp.bitbucket.cloud.model.BitbucketProjectBranchingModel.*;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("BitbucketProjectBranchingModel")
class BitbucketProjectBranchingModelTest {

    @Test
    @DisplayName("should create record with all fields")
    void shouldCreateRecordWithAllFields() {
        DevelopmentDto development = new DevelopmentDto("develop", false);
        ProductionDto production = new ProductionDto("main", true);
        List<BranchTypeDto> branchTypes = List.of(
                new BranchTypeDto("feature", "feature/"),
                new BranchTypeDto("bugfix", "bugfix/")
        );

        BitbucketProjectBranchingModel model = new BitbucketProjectBranchingModel(
                "project_branching_model",
                development,
                production,
                branchTypes,
                Map.of()
        );

        assertThat(model.type()).isEqualTo("project_branching_model");
        assertThat(model.development()).isEqualTo(development);
        assertThat(model.production()).isEqualTo(production);
        assertThat(model.branchTypes()).hasSize(2);
    }

    @Nested
    @DisplayName("DevelopmentDto")
    class DevelopmentDtoTests {

        @Test
        @DisplayName("should create with name and useMainbranch")
        void shouldCreateWithNameAndUseMainbranch() {
            DevelopmentDto dto = new DevelopmentDto("develop", true);

            assertThat(dto.name()).isEqualTo("develop");
            assertThat(dto.useMainbranch()).isTrue();
        }

        @Test
        @DisplayName("should handle non-mainbranch development")
        void shouldHandleNonMainbranchDevelopment() {
            DevelopmentDto dto = new DevelopmentDto("develop", false);

            assertThat(dto.useMainbranch()).isFalse();
        }
    }

    @Nested
    @DisplayName("ProductionDto")
    class ProductionDtoTests {

        @Test
        @DisplayName("should create with name and useMainbranch")
        void shouldCreateWithNameAndUseMainbranch() {
            ProductionDto dto = new ProductionDto("main", true);

            assertThat(dto.name()).isEqualTo("main");
            assertThat(dto.useMainbranch()).isTrue();
        }
    }

    @Nested
    @DisplayName("BranchTypeDto")
    class BranchTypeDtoTests {

        @Test
        @DisplayName("should create with kind and prefix")
        void shouldCreateWithKindAndPrefix() {
            BranchTypeDto dto = new BranchTypeDto("feature", "feature/");

            assertThat(dto.kind()).isEqualTo("feature");
            assertThat(dto.prefix()).isEqualTo("feature/");
        }
    }

    @Test
    @DisplayName("should handle empty branch types")
    void shouldHandleEmptyBranchTypes() {
        BitbucketProjectBranchingModel model = new BitbucketProjectBranchingModel(
                "type", null, null, List.of(), null
        );

        assertThat(model.branchTypes()).isEmpty();
    }
}

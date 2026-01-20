package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.mcp.bitbucket.cloud.model.BitbucketBranchingModel.*;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("BitbucketBranchingModel")
class BitbucketBranchingModelTest {

    @Test
    @DisplayName("should create record with all fields")
    void shouldCreateRecordWithAllFields() {
        DevelopmentDto development = new DevelopmentDto("develop", null, false);
        ProductionDto production = new ProductionDto("main", null, true);
        List<BranchTypeDto> branchTypes = List.of(
                new BranchTypeDto("feature", "feature/"),
                new BranchTypeDto("bugfix", "bugfix/")
        );

        BitbucketBranchingModel model = new BitbucketBranchingModel(
                "branching_model",
                development,
                production,
                branchTypes,
                Map.of()
        );

        assertThat(model.type()).isEqualTo("branching_model");
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
            DevelopmentDto dto = new DevelopmentDto("develop", null, true);

            assertThat(dto.name()).isEqualTo("develop");
            assertThat(dto.branch()).isNull();
            assertThat(dto.useMainbranch()).isTrue();
        }
    }

    @Nested
    @DisplayName("ProductionDto")
    class ProductionDtoTests {

        @Test
        @DisplayName("should create with name and useMainbranch")
        void shouldCreateWithNameAndUseMainbranch() {
            ProductionDto dto = new ProductionDto("main", null, true);

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

        @Test
        @DisplayName("should support different branch types")
        void shouldSupportDifferentBranchTypes() {
            BranchTypeDto feature = new BranchTypeDto("feature", "feature/");
            BranchTypeDto bugfix = new BranchTypeDto("bugfix", "bugfix/");
            BranchTypeDto release = new BranchTypeDto("release", "release/");
            BranchTypeDto hotfix = new BranchTypeDto("hotfix", "hotfix/");

            assertThat(feature.kind()).isEqualTo("feature");
            assertThat(bugfix.kind()).isEqualTo("bugfix");
            assertThat(release.kind()).isEqualTo("release");
            assertThat(hotfix.kind()).isEqualTo("hotfix");
        }
    }
}

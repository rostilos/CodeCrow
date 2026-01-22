package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("BitbucketBranchReference")
class BitbucketBranchReferenceTest {

    @Nested
    @DisplayName("BitbucketBranchReference record")
    class BranchReferenceRecord {

        @Test
        @DisplayName("should create with all fields")
        void shouldCreateWithAllFields() {
            BitbucketBranchReference.BranchDto branchDto = new BitbucketBranchReference.BranchDto("main", null);
            BitbucketBranchReference.CommitDto commitDto = new BitbucketBranchReference.CommitDto("abc123", null);
            BitbucketRepository repository = new BitbucketRepository(
                    "repository", "{uuid}", "test-repo", "Test Repo", "ws/test-repo", null, false,
                    null, null, 0L, null, false, false, null,
                    null, null, null, null, null, null, null
            );
            
            BitbucketBranchReference ref = new BitbucketBranchReference(branchDto, commitDto, repository);
            
            assertThat(ref.branch()).isNotNull();
            assertThat(ref.branch().name()).isEqualTo("main");
            assertThat(ref.commit()).isNotNull();
            assertThat(ref.commit().hash()).isEqualTo("abc123");
            assertThat(ref.repository()).isNotNull();
            assertThat(ref.repository().slug()).isEqualTo("test-repo");
        }

        @Test
        @DisplayName("should create with null values")
        void shouldCreateWithNullValues() {
            BitbucketBranchReference ref = new BitbucketBranchReference(null, null, null);
            
            assertThat(ref.branch()).isNull();
            assertThat(ref.commit()).isNull();
            assertThat(ref.repository()).isNull();
        }
    }

    @Nested
    @DisplayName("BranchDto nested record")
    class BranchDtoRecord {

        @Test
        @DisplayName("should create with all fields")
        void shouldCreateWithAllFields() {
            Map<String, BitbucketLink> links = Map.of("html", new BitbucketLink("https://url", "html"));
            
            BitbucketBranchReference.BranchDto dto = new BitbucketBranchReference.BranchDto("feature-branch", links);
            
            assertThat(dto.name()).isEqualTo("feature-branch");
            assertThat(dto.links()).containsKey("html");
        }

        @Test
        @DisplayName("should be equal for same values")
        void shouldBeEqualForSameValues() {
            BitbucketBranchReference.BranchDto dto1 = new BitbucketBranchReference.BranchDto("main", null);
            BitbucketBranchReference.BranchDto dto2 = new BitbucketBranchReference.BranchDto("main", null);
            
            assertThat(dto1).isEqualTo(dto2);
        }
    }

    @Nested
    @DisplayName("CommitDto nested record")
    class CommitDtoRecord {

        @Test
        @DisplayName("should create with all fields")
        void shouldCreateWithAllFields() {
            Map<String, BitbucketLink> links = Map.of("diff", new BitbucketLink("https://diff.url", "diff"));
            
            BitbucketBranchReference.CommitDto dto = new BitbucketBranchReference.CommitDto("def456", links);
            
            assertThat(dto.hash()).isEqualTo("def456");
            assertThat(dto.links()).containsKey("diff");
        }

        @Test
        @DisplayName("should be equal for same values")
        void shouldBeEqualForSameValues() {
            BitbucketBranchReference.CommitDto dto1 = new BitbucketBranchReference.CommitDto("hash", null);
            BitbucketBranchReference.CommitDto dto2 = new BitbucketBranchReference.CommitDto("hash", null);
            
            assertThat(dto1).isEqualTo(dto2);
        }
    }
}

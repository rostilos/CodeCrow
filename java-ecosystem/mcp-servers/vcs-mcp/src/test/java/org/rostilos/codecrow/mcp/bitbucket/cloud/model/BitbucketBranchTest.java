package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("BitbucketBranch")
class BitbucketBranchTest {

    @Test
    @DisplayName("should create with all fields")
    void shouldCreateWithAllFields() {
        BitbucketPullRequest.BitbucketCommit commit = new BitbucketPullRequest.BitbucketCommit("abc123");
        
        BitbucketBranch branch = new BitbucketBranch("main", "branch", commit);
        
        assertThat(branch.name()).isEqualTo("main");
        assertThat(branch.type()).isEqualTo("branch");
        assertThat(branch.target()).isNotNull();
        assertThat(branch.target().hash()).isEqualTo("abc123");
    }

    @Test
    @DisplayName("should create with null values")
    void shouldCreateWithNullValues() {
        BitbucketBranch branch = new BitbucketBranch(null, null, null);
        
        assertThat(branch.name()).isNull();
        assertThat(branch.type()).isNull();
        assertThat(branch.target()).isNull();
    }

    @Test
    @DisplayName("should be equal for same values")
    void shouldBeEqualForSameValues() {
        BitbucketBranch branch1 = new BitbucketBranch("feature", "branch", null);
        BitbucketBranch branch2 = new BitbucketBranch("feature", "branch", null);
        
        assertThat(branch1).isEqualTo(branch2);
        assertThat(branch1.hashCode()).isEqualTo(branch2.hashCode());
    }
}

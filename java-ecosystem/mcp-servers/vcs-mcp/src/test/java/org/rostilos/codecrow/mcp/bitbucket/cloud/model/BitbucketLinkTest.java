package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("BitbucketLink")
class BitbucketLinkTest {

    @Test
    @DisplayName("should create with all fields")
    void shouldCreateWithAllFields() {
        BitbucketLink link = new BitbucketLink("https://api.bitbucket.org/2.0/repos/test", "self");
        
        assertThat(link.href()).isEqualTo("https://api.bitbucket.org/2.0/repos/test");
        assertThat(link.name()).isEqualTo("self");
    }

    @Test
    @DisplayName("should create with null values")
    void shouldCreateWithNullValues() {
        BitbucketLink link = new BitbucketLink(null, null);
        
        assertThat(link.href()).isNull();
        assertThat(link.name()).isNull();
    }

    @Test
    @DisplayName("should be equal for same values")
    void shouldBeEqualForSameValues() {
        BitbucketLink link1 = new BitbucketLink("https://url", "name");
        BitbucketLink link2 = new BitbucketLink("https://url", "name");
        
        assertThat(link1).isEqualTo(link2);
        assertThat(link1.hashCode()).isEqualTo(link2.hashCode());
    }

    @Test
    @DisplayName("should not be equal for different values")
    void shouldNotBeEqualForDifferentValues() {
        BitbucketLink link1 = new BitbucketLink("https://url1", "name");
        BitbucketLink link2 = new BitbucketLink("https://url2", "name");
        
        assertThat(link1).isNotEqualTo(link2);
    }
}

package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("BitbucketRepository")
class BitbucketRepositoryTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    @DisplayName("should create with all fields")
    void shouldCreateWithAllFields() {
        JsonNode linkNode = objectMapper.createObjectNode().put("href", "https://repo.url");
        Map<String, JsonNode> links = Map.of("self", linkNode);
        
        BitbucketAccount owner = new BitbucketAccount();
        owner.username = "owner";
        
        BitbucketRepository repo = new BitbucketRepository(
                "repository",
                "{uuid}",
                "my-repo",
                "My Repository",
                "workspace/my-repo",
                "A test repository",
                true,
                "2024-01-01T00:00:00Z",
                "2024-01-15T00:00:00Z",
                1024L,
                "java",
                true,
                false,
                "no_forks",
                owner,
                null,
                null,
                null,
                "https://website.com",
                "git",
                links
        );
        
        assertThat(repo.type()).isEqualTo("repository");
        assertThat(repo.uuid()).isEqualTo("{uuid}");
        assertThat(repo.slug()).isEqualTo("my-repo");
        assertThat(repo.name()).isEqualTo("My Repository");
        assertThat(repo.fullName()).isEqualTo("workspace/my-repo");
        assertThat(repo.description()).isEqualTo("A test repository");
        assertThat(repo.isPrivate()).isTrue();
        assertThat(repo.createdOn()).isEqualTo("2024-01-01T00:00:00Z");
        assertThat(repo.updatedOn()).isEqualTo("2024-01-15T00:00:00Z");
        assertThat(repo.size()).isEqualTo(1024L);
        assertThat(repo.language()).isEqualTo("java");
        assertThat(repo.hasIssues()).isTrue();
        assertThat(repo.hasWiki()).isFalse();
        assertThat(repo.forkPolicy()).isEqualTo("no_forks");
        assertThat(repo.owner()).isNotNull();
        assertThat(repo.website()).isEqualTo("https://website.com");
        assertThat(repo.scm()).isEqualTo("git");
        assertThat(repo.links()).containsKey("self");
    }

    @Test
    @DisplayName("should create with null values")
    void shouldCreateWithNullValues() {
        BitbucketRepository repo = new BitbucketRepository(
                null, null, null, null, null, null, false,
                null, null, 0L, null, false, false, null,
                null, null, null, null, null, null, null
        );
        
        assertThat(repo.type()).isNull();
        assertThat(repo.uuid()).isNull();
        assertThat(repo.slug()).isNull();
        assertThat(repo.name()).isNull();
        assertThat(repo.fullName()).isNull();
        assertThat(repo.description()).isNull();
        assertThat(repo.isPrivate()).isFalse();
    }

    @Test
    @DisplayName("should be equal for same values")
    void shouldBeEqualForSameValues() {
        BitbucketRepository repo1 = new BitbucketRepository(
                "repository", "{uuid}", "slug", "name", "full", "desc", true,
                "2024-01-01", "2024-01-02", 100L, "java", true, true, "allow_forks",
                null, null, null, null, "https://web", "git", null
        );
        BitbucketRepository repo2 = new BitbucketRepository(
                "repository", "{uuid}", "slug", "name", "full", "desc", true,
                "2024-01-01", "2024-01-02", 100L, "java", true, true, "allow_forks",
                null, null, null, null, "https://web", "git", null
        );
        
        assertThat(repo1).isEqualTo(repo2);
        assertThat(repo1.hashCode()).isEqualTo(repo2.hashCode());
    }
}

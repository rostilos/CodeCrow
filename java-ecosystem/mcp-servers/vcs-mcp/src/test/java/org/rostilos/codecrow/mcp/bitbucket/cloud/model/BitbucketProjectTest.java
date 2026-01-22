package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("BitbucketProject")
class BitbucketProjectTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    @DisplayName("should create with all fields")
    void shouldCreateWithAllFields() {
        JsonNode linkNode = objectMapper.createObjectNode().put("href", "https://link.url");
        Map<String, JsonNode> links = Map.of("self", linkNode);
        
        BitbucketProject project = new BitbucketProject(
                "{uuid-123}",
                "PROJ",
                "My Project",
                "A test project",
                true,
                "project",
                links
        );
        
        assertThat(project.uuid()).isEqualTo("{uuid-123}");
        assertThat(project.key()).isEqualTo("PROJ");
        assertThat(project.name()).isEqualTo("My Project");
        assertThat(project.description()).isEqualTo("A test project");
        assertThat(project.isPrivate()).isTrue();
        assertThat(project.type()).isEqualTo("project");
        assertThat(project.links()).containsKey("self");
    }

    @Test
    @DisplayName("should create with null values")
    void shouldCreateWithNullValues() {
        BitbucketProject project = new BitbucketProject(null, null, null, null, false, null, null);
        
        assertThat(project.uuid()).isNull();
        assertThat(project.key()).isNull();
        assertThat(project.name()).isNull();
        assertThat(project.description()).isNull();
        assertThat(project.isPrivate()).isFalse();
        assertThat(project.type()).isNull();
        assertThat(project.links()).isNull();
    }

    @Test
    @DisplayName("should be equal for same values")
    void shouldBeEqualForSameValues() {
        BitbucketProject project1 = new BitbucketProject("uuid", "KEY", "name", "desc", true, "project", null);
        BitbucketProject project2 = new BitbucketProject("uuid", "KEY", "name", "desc", true, "project", null);
        
        assertThat(project1).isEqualTo(project2);
        assertThat(project1.hashCode()).isEqualTo(project2.hashCode());
    }
}

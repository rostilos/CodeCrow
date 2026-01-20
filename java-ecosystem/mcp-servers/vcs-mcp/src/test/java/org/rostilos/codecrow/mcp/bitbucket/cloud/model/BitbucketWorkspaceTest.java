package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("BitbucketWorkspace")
class BitbucketWorkspaceTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    @DisplayName("should create with all fields")
    void shouldCreateWithAllFields() {
        JsonNode linkNode = objectMapper.createObjectNode().put("href", "https://workspace.url");
        Map<String, JsonNode> links = Map.of("html", linkNode);
        
        BitbucketWorkspace workspace = new BitbucketWorkspace(
                "{workspace-uuid}",
                "My Workspace",
                "my-workspace",
                "workspace",
                links
        );
        
        assertThat(workspace.uuid()).isEqualTo("{workspace-uuid}");
        assertThat(workspace.name()).isEqualTo("My Workspace");
        assertThat(workspace.slug()).isEqualTo("my-workspace");
        assertThat(workspace.type()).isEqualTo("workspace");
        assertThat(workspace.links()).containsKey("html");
    }

    @Test
    @DisplayName("should create with null values")
    void shouldCreateWithNullValues() {
        BitbucketWorkspace workspace = new BitbucketWorkspace(null, null, null, null, null);
        
        assertThat(workspace.uuid()).isNull();
        assertThat(workspace.name()).isNull();
        assertThat(workspace.slug()).isNull();
        assertThat(workspace.type()).isNull();
        assertThat(workspace.links()).isNull();
    }

    @Test
    @DisplayName("should be equal for same values")
    void shouldBeEqualForSameValues() {
        BitbucketWorkspace ws1 = new BitbucketWorkspace("uuid", "name", "slug", "workspace", null);
        BitbucketWorkspace ws2 = new BitbucketWorkspace("uuid", "name", "slug", "workspace", null);
        
        assertThat(ws1).isEqualTo(ws2);
        assertThat(ws1.hashCode()).isEqualTo(ws2.hashCode());
    }
}

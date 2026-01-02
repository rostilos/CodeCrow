package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.JsonNode;
import java.util.Map;

@JsonIgnoreProperties(ignoreUnknown = true)
public record BitbucketProject(
        String uuid,
        String key,
        String name,
        String description,
        @JsonProperty("is_private") boolean isPrivate,
        String type,
        Map<String, JsonNode> links
) {}
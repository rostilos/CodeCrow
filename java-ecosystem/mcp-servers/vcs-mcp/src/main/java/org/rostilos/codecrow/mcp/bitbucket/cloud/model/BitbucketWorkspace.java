package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.databind.JsonNode;
import java.util.Map;

@JsonIgnoreProperties(ignoreUnknown = true)
public record BitbucketWorkspace(
        String uuid,
        String name,
        String slug,
        String type,
        Map<String, JsonNode> links
) {}
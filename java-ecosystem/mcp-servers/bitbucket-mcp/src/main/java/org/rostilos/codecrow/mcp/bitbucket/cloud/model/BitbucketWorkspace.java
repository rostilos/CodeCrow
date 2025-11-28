package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import java.util.Map;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.databind.JsonNode;

@JsonIgnoreProperties(ignoreUnknown = true)
public class BitbucketWorkspace {
    public String uuid;
    public String name;
    public String slug;
    public String type; // "workspace"
    public Map<String, JsonNode> links;
}

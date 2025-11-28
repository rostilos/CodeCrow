package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import java.util.Map;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.JsonNode;

@JsonIgnoreProperties(ignoreUnknown = true)
public class BitbucketProject {
    public String uuid;
    public String key;
    public String name;
    public String description;
    @JsonProperty("is_private")
    public boolean isPrivate;
    public String type;
    public Map<String, JsonNode> links;
}

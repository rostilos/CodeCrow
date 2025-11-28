package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import java.util.Map;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

@JsonIgnoreProperties(ignoreUnknown = true)
public class BitbucketAccount {
    public String uuid;
    public String username;
    @JsonProperty("display_name")
    public String displayName;
    @JsonProperty("account_id")
    public String accountId;
    public String nickname;
    public String type;
    public Map<String, BitbucketLink> links;
}

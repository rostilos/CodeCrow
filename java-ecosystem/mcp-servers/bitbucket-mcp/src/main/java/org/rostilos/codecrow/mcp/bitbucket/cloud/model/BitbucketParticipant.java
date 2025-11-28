package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

@JsonIgnoreProperties(ignoreUnknown = true)
public class BitbucketParticipant {
    public String type;
    public BitbucketAccount user;
    public String role; // "PARTICIPANT" | "REVIEWER"
    public boolean approved;
    public String state; // "approved" | "changes_requested" | null
    @JsonProperty("participated_on")
    public String participatedOn;
}

package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

@JsonIgnoreProperties(ignoreUnknown = true)
public record BitbucketParticipant(
        String type,
        BitbucketAccount user,
        String role,        // Could be changed to an Enum: ParticipantRole
        boolean approved,
        String state,       // Could be changed to an Enum: ParticipantState
        @JsonProperty("participated_on") String participatedOn
) {}
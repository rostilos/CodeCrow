package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import java.util.List;
import java.util.Map; // Don't forget this if you need it for other unknown fields

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

@JsonIgnoreProperties(ignoreUnknown = true) // Consider keeping this to ignore other unexpected fields
public class BitbucketPullRequest {
    @JsonProperty("type")
    public String type;

    @JsonProperty("id")
    public long id;
    @JsonProperty("title")
    public String title;
    @JsonProperty("state")
    public String state;
    public BitbucketAccount author;
    public BitbucketBranchReference source;
    public BitbucketBranchReference destination;
    @JsonProperty("created_on")
    public String createdOn;
    @JsonProperty("updated_on")
    public String updatedOn;
    @JsonProperty("comment_count")
    public int commentCount;
    @JsonProperty("task_count")
    public int taskCount;
    @JsonProperty("close_source_branch")
    public boolean closeSourceBranch;
    public List<BitbucketAccount> reviewers;
    public List<BitbucketParticipant> participants;
    public BitbucketSummary summary;

    public BitbucketRenderedContent rendered;

    public BitbucketCommit merge_commit;
    public BitbucketAccount closedBy;
    public String reason;
    public boolean draft;
    public boolean queued;

    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class BitbucketSummary {
        public String raw;
        public String markup;
        public String html;
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class BitbucketRenderedContent {
        public BitbucketSummary title;
        public BitbucketSummary description;
        public BitbucketSummary reason;
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class BitbucketCommit {
        public String hash;
    }
}

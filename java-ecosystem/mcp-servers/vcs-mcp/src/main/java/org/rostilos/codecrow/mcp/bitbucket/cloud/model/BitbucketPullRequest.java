package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.List;

@JsonIgnoreProperties(ignoreUnknown = true)
public record BitbucketPullRequest(
        long id,
        String title,
        String state, // Ideally an Enum: OPEN, MERGED, DECLINED, SUPERSEDED
        BitbucketAccount author,
        BitbucketBranchReference source,
        BitbucketBranchReference destination,
        @JsonProperty("created_on") String createdOn,
        @JsonProperty("updated_on") String updatedOn,
        @JsonProperty("comment_count") int commentCount,
        @JsonProperty("task_count") int taskCount,
        @JsonProperty("close_source_branch") boolean closeSourceBranch,
        List<BitbucketAccount> reviewers,
        List<BitbucketParticipant> participants,
        BitbucketSummary summary,
        BitbucketRenderedContent rendered,
        @JsonProperty("merge_commit") BitbucketCommit mergeCommit,
        @JsonProperty("closed_by") BitbucketAccount closedBy,
        String reason,
        boolean draft,
        boolean queued
) {
    public record BitbucketSummary(String raw, String markup, String html) {}

    public record BitbucketRenderedContent(
            BitbucketSummary title,
            BitbucketSummary description,
            BitbucketSummary reason
    ) {}

    public record BitbucketCommit(String hash) {}
}
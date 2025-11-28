package org.rostilos.codecrow.vcsclient.bitbucket.model.comment;

import com.fasterxml.jackson.annotation.JsonProperty;

public record BitbucketSummarizeComment(
        @JsonProperty("content") BitbucketCommentContent content
) {
}

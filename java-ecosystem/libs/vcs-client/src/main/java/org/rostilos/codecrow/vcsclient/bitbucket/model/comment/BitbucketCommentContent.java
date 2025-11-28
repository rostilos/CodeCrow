package org.rostilos.codecrow.vcsclient.bitbucket.model.comment;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonProperty;

public record BitbucketCommentContent(String raw) {
    @JsonCreator
    public BitbucketCommentContent(@JsonProperty("raw") String raw) {
        this.raw = raw;
    }
}

package org.rostilos.codecrow.vcsclient.bitbucket.cloud.dto.response;

import org.jetbrains.annotations.NotNull;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.SearchBitbucketCloudReposAction;
import java.util.List;

public record RepositorySearchResult(
        List<SearchBitbucketCloudReposAction.Repository> repositories,
        int currentPage,
        int pageSize,
        int currentPageSize,
        Integer totalSize,
        boolean hasNext,
        boolean hasPrevious
) {

    public Integer getTotalPages() {
        if (totalSize != null && pageSize > 0) {
            return (int) Math.ceil((double) totalSize / pageSize);
        }
        return null;
    }

    @NotNull
    @Override
    public String toString() {
        return String.format("RepositorySearchResult{repositories=%d, currentPage=%d, pageSize=%d, totalSize=%s, hasNext=%s, hasPrevious=%s}",
                repositories.size(), currentPage, pageSize, totalSize, hasNext, hasPrevious);
    }
}

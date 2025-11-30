package org.rostilos.codecrow.vcsclient.model;

import java.util.List;

/**
 * Paginated result of VCS repositories.
 */
public record VcsRepositoryPage(
    /**
     * List of repositories on this page.
     */
    List<VcsRepository> items,
    
    /**
     * Current page number (1-based).
     */
    int page,
    
    /**
     * Number of items per page.
     */
    int pageSize,
    
    /**
     * Number of items on this page.
     */
    int itemCount,
    
    /**
     * Total count of items across all pages (null if unknown).
     */
    Integer totalCount,
    
    /**
     * Whether there is a next page.
     */
    boolean hasNext,
    
    /**
     * Whether there is a previous page.
     */
    boolean hasPrevious
) {
    /**
     * Create an empty page.
     */
    public static VcsRepositoryPage empty() {
        return new VcsRepositoryPage(List.of(), 1, 0, 0, 0, false, false);
    }
}

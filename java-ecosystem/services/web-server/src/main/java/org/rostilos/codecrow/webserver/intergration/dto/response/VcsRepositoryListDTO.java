package org.rostilos.codecrow.webserver.intergration.dto.response;

import org.rostilos.codecrow.vcsclient.model.VcsRepository;

import java.util.List;

/**
 * DTO for repository list with pagination.
 */
public record VcsRepositoryListDTO(
    List<VcsRepositoryDTO> items,
    int page,
    int pageSize,
    int itemCount,
    Integer totalCount,
    boolean hasNext,
    boolean hasPrevious
) {
    /**
     * Single repository item DTO.
     */
    public record VcsRepositoryDTO(
        String id,
        String slug,
        String name,
        String fullName,
        String description,
        boolean isPrivate,
        String defaultBranch,
        String cloneUrl,
        String htmlUrl,
        String namespace,
        String avatarUrl,
        boolean isOnboarded
    ) {
        /**
         * Create DTO from VcsRepository model.
         */
        public static VcsRepositoryDTO fromModel(VcsRepository repo, boolean isOnboarded) {
            return new VcsRepositoryDTO(
                repo.id(),
                repo.slug(),
                repo.name(),
                repo.fullName(),
                repo.description(),
                repo.isPrivate(),
                repo.defaultBranch(),
                repo.cloneUrl(),
                repo.htmlUrl(),
                repo.namespace(),
                repo.avatarUrl(),
                isOnboarded
            );
        }
    }
}

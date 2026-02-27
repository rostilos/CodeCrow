package org.rostilos.codecrow.core.persistence.repository.pullrequest;

import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.model.pullrequest.PullRequestState;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface PullRequestRepository extends JpaRepository<PullRequest, Long> {
    List<PullRequest> findByProject_Id(Long workspaceId);
    List<PullRequest> findByProject_IdOrderByPrNumberDesc(Long projectId);
    Page<PullRequest> findByProject_IdOrderByPrNumberDesc(Long projectId, Pageable pageable);
    Optional<PullRequest> findByPrNumberAndProject_id(Long prId, Long projectId);
    void deleteByProject_Id(Long projectId);

    /**
     * Find all open (non-merged, non-declined) PRs targeting a specific branch.
     * Used by hybrid branch analysis to determine if unanalyzed commits
     * are already covered by an open PR's analysis.
     */
    @Query("SELECT pr FROM PullRequest pr WHERE pr.project.id = :projectId " +
           "AND pr.targetBranchName = :targetBranch AND pr.state = :state")
    List<PullRequest> findByProjectIdAndTargetBranchNameAndState(
            @Param("projectId") Long projectId,
            @Param("targetBranch") String targetBranch,
            @Param("state") PullRequestState state);
}

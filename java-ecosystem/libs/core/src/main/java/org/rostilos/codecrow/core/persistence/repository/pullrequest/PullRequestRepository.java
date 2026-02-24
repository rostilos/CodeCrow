package org.rostilos.codecrow.core.persistence.repository.pullrequest;

import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
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
}

package org.rostilos.codecrow.core.persistence.repository.pullrequest;

import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface PullRequestRepository extends JpaRepository<PullRequest, Long> {
    List<PullRequest> findByProject_Id(Long workspaceId);
    Optional<PullRequest> findByPrNumberAndProject_id(Long prId, Long projectId);
}

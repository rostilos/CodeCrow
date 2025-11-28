package org.rostilos.codecrow.core.persistence.repository.branch;

import org.rostilos.codecrow.core.model.branch.Branch;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.Optional;
import java.util.List;

@Repository
public interface BranchRepository extends JpaRepository<Branch, Long> {

    Optional<Branch> findByProjectIdAndBranchName(Long projectId, String branchName);

    Optional<Branch> findByProjectIdAndCommitHash(Long projectId, String commitHash);

    List<Branch> findByProjectId(Long projectId);

    void deleteByProjectId(Long projectId);
}

package org.rostilos.codecrow.filecontent.persistence;

import org.rostilos.codecrow.filecontent.model.BranchFile;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.Optional;
import java.util.List;

@Repository
public interface BranchFileRepository extends JpaRepository<BranchFile, Long> {

    Optional<BranchFile> findByProjectIdAndBranchNameAndFilePath(Long projectId, String branchName, String filePath);

    List<BranchFile> findByProjectIdAndBranchName(Long projectId, String branchName);

    List<BranchFile> findByProjectId(Long projectId);

    void deleteByProjectId(Long projectId);
}

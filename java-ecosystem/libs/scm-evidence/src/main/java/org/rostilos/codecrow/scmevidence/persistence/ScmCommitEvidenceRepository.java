package org.rostilos.codecrow.scmevidence.persistence;

import org.rostilos.codecrow.scmevidence.model.ScmCommitEvidence;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface ScmCommitEvidenceRepository
        extends JpaRepository<ScmCommitEvidence, Long> {
    Optional<ScmCommitEvidence> findByProjectIdAndCommitHash(
            Long projectId, String commitHash);
    List<ScmCommitEvidence> findByProjectIdAndCommitHashIn(
            Long projectId, List<String> commitHashes);
    List<ScmCommitEvidence> findByProjectIdAndPatchIdIn(
            Long projectId, List<String> patchIds);
}

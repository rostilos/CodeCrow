package org.rostilos.codecrow.scmevidence.persistence;

import org.rostilos.codecrow.scmevidence.model.ScmAnalysisReceipt;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface ScmAnalysisReceiptRepository
        extends JpaRepository<ScmAnalysisReceipt, Long> {
    boolean existsByProjectIdAndCommitEvidenceIdAndContextKey(
            Long projectId, Long commitEvidenceId, String contextKey);
    List<ScmAnalysisReceipt> findByProjectIdAndCommitEvidencePatchIdIn(
            Long projectId, List<String> patchIds);
}

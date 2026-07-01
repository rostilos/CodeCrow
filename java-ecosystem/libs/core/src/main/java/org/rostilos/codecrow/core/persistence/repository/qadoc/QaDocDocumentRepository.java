package org.rostilos.codecrow.core.persistence.repository.qadoc;

import org.rostilos.codecrow.core.model.qadoc.QaDocDocument;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.Optional;

@Repository
public interface QaDocDocumentRepository extends JpaRepository<QaDocDocument, Long> {

    Optional<QaDocDocument> findByProjectIdAndPrNumber(Long projectId, Long prNumber);

    @Modifying
    @Query("DELETE FROM QaDocDocument q WHERE q.project.id = :projectId")
    void deleteByProjectId(@Param("projectId") Long projectId);
}

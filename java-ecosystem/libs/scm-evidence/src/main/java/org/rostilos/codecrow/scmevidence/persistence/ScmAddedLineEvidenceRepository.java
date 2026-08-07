package org.rostilos.codecrow.scmevidence.persistence;

import org.rostilos.codecrow.scmevidence.model.ScmAddedLineEvidence;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;

public interface ScmAddedLineEvidenceRepository
        extends JpaRepository<ScmAddedLineEvidence, Long> {
    @Query("""
            SELECT line FROM ScmAddedLineEvidence line
            JOIN FETCH line.commitEvidence evidence
            WHERE evidence.projectId = :projectId
              AND evidence.commitHash IN :commitHashes
              AND line.filePath = :filePath
              AND line.lineHash = :lineHash
            """)
    List<ScmAddedLineEvidence> findMatchingLines(
            @Param("projectId") Long projectId,
            @Param("commitHashes") List<String> commitHashes,
            @Param("filePath") String filePath,
            @Param("lineHash") String lineHash);
}

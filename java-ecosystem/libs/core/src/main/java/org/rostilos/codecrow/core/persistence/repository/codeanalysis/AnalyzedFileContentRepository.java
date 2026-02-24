package org.rostilos.codecrow.core.persistence.repository.codeanalysis;

import org.rostilos.codecrow.core.model.codeanalysis.AnalyzedFileContent;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.Optional;

@Repository
public interface AnalyzedFileContentRepository extends JpaRepository<AnalyzedFileContent, Long> {

    /**
     * Find content by its SHA-256 hash (the deduplication key).
     */
    Optional<AnalyzedFileContent> findByContentHash(String contentHash);
}

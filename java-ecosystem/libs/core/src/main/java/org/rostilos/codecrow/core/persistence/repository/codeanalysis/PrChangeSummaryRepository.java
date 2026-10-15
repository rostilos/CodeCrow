package org.rostilos.codecrow.core.persistence.repository.codeanalysis;

import org.rostilos.codecrow.core.model.codeanalysis.PrChangeSummary;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.Optional;

public interface PrChangeSummaryRepository extends JpaRepository<PrChangeSummary, Long> {
    Optional<PrChangeSummary> findFirstByProjectIdAndPrNumberAndSourceRevisionOrderByCreatedAtDesc(
            Long projectId, Long prNumber, String sourceRevision);
}

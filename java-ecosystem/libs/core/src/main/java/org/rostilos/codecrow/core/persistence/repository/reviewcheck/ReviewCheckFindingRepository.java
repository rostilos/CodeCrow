package org.rostilos.codecrow.core.persistence.repository.reviewcheck;

import org.rostilos.codecrow.core.model.reviewcheck.ReviewCheckFinding;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;
import java.util.Optional;

public interface ReviewCheckFindingRepository extends JpaRepository<ReviewCheckFinding, Long> {
    Optional<ReviewCheckFinding> findByIdAndResult_Analysis_Project_Id(Long id, Long projectId);
    List<ReviewCheckFinding> findByResult_Analysis_Project_IdAndResult_Analysis_PrNumberAndResult_CheckIdAndFindingKey(
            Long projectId, Long prNumber, String checkId, String findingKey);
}

package org.rostilos.codecrow.core.service.reviewcheck;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.reviewcheck.ReviewCheckResult;
import org.rostilos.codecrow.core.persistence.repository.reviewcheck.ReviewCheckResultRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Objects;

@Service
@Transactional
public class ReviewCheckResultService {
    private final ReviewCheckResultRepository repository;

    public ReviewCheckResultService(ReviewCheckResultRepository repository) { this.repository = repository; }

    @Transactional(readOnly = true)
    public List<ReviewCheckResult> findForAnalysis(CodeAnalysis analysis) {
        if (analysis.getId() == null) return List.of();
        return repository.findByAnalysis_Project_IdAndAnalysis_IdOrderByCheckId(analysis.getProject().getId(), analysis.getId());
    }

    @Transactional(readOnly = true)
    public List<ReviewCheckResult> findPrevious(CodeAnalysis analysis) {
        return repository.findPrevious(analysis.getProject().getId(), analysis.getPrNumber(), analysis.getPrVersion());
    }

    public void save(CodeAnalysis analysis, List<ReviewCheckResult> results) {
        results.forEach(result -> result.setAnalysis(analysis));
        repository.saveAllAndFlush(results);
    }

    public void copyForAnalysis(CodeAnalysis source, CodeAnalysis target) {
        if (!Objects.equals(source.getProject().getId(), target.getProject().getId())) {
            throw new IllegalArgumentException("Review checks cannot be copied between projects");
        }
        save(target, findForAnalysis(source).stream()
                .map(result -> result.copy(Objects.equals(source.getPrNumber(), target.getPrNumber()))).toList());
    }
}

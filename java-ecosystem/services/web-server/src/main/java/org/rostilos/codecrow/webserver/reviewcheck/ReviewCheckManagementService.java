package org.rostilos.codecrow.webserver.reviewcheck;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.reviewcheck.ReviewCheckFindingRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.core.service.reviewcheck.ReviewCheckResultService;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.OffsetDateTime;
import java.util.LinkedHashMap;

@Service
@Transactional
public class ReviewCheckManagementService {
    private final CodeAnalysisRepository analyses;
    private final ReviewCheckFindingRepository findings;
    private final ReviewCheckResultService results;
    private final CodeAnalysisService analysisService;

    public ReviewCheckManagementService(CodeAnalysisRepository analyses, ReviewCheckFindingRepository findings,
                              ReviewCheckResultService results, CodeAnalysisService analysisService) {
        this.analyses = analyses; this.findings = findings; this.results = results; this.analysisService = analysisService;
    }

    @Transactional(readOnly = true)
    public ReviewCheckResponse get(Long projectId, Long prNumber, int version) {
        var analysis = (version > 0 ? analyses.findByProjectIdAndPrNumberAndPrVersion(projectId, prNumber, version)
                : analyses.findByProjectIdAndPrNumberWithMaxPrVersion(projectId, prNumber))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND));
        return new ReviewCheckResponse(analysis.getId(), analysis.getPrVersion(),
                results.findForAnalysis(analysis).stream().map(ReviewCheckResponse.Check::from).toList());
    }

    public void updateStatus(Long projectId, Long findingId, boolean resolved, String reason) {
        var finding = findings.findByIdAndResult_Analysis_Project_Id(findingId, projectId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND));
        var result = finding.getResult();
        var related = findings.findByResult_Analysis_Project_IdAndResult_Analysis_PrNumberAndResult_CheckIdAndFindingKey(
                projectId, result.getAnalysis().getPrNumber(), result.getCheckId(), finding.getFindingKey());
        var affected = new LinkedHashMap<Long, CodeAnalysis>();
        var now = OffsetDateTime.now();
        for (var item : related) {
            item.setResolved(resolved); item.setManualStatusResolved(resolved); item.setManualStatusUpdatedAt(now);
            item.setResolvedDescription(reason);
            var analysis = item.getResult().getAnalysis(); affected.put(analysis.getId(), analysis);
        }
        findings.saveAllAndFlush(related);
        affected.values().forEach(analysisService::saveAnalysis);
    }
}

package org.rostilos.codecrow.webserver.publicshare.qadoc;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.qadoc.QaDocDocument;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.core.service.QaDocDocumentService;
import org.rostilos.codecrow.core.service.qadoc.QaDocContentParser;
import org.rostilos.codecrow.core.service.qadoc.QaDocPublicShareResource;
import org.rostilos.codecrow.webserver.analysis.dto.response.QaDocTestCaseResponse;
import org.rostilos.codecrow.webserver.publicshare.PublicShareResourceProvider;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.util.Objects;
import java.util.Optional;

@Component
public class QaDocTestCasesShareProvider implements PublicShareResourceProvider {

    private final QaDocDocumentService qaDocDocumentService;
    private final CodeAnalysisService codeAnalysisService;

    public QaDocTestCasesShareProvider(QaDocDocumentService qaDocDocumentService,
                                       CodeAnalysisService codeAnalysisService) {
        this.qaDocDocumentService = qaDocDocumentService;
        this.codeAnalysisService = codeAnalysisService;
    }

    @Override
    public String resourceType() {
        return QaDocPublicShareResource.TEST_CASES;
    }

    @Override
    @Transactional(readOnly = true)
    public Optional<QaDocTestCasesPublicPreview> getPublicPreview(String resourceKey) {
        Long documentId;
        try {
            documentId = Long.valueOf(resourceKey);
        } catch (NumberFormatException ignored) {
            return Optional.empty();
        }

        return qaDocDocumentService.findDocumentById(documentId)
                .flatMap(this::toPublicPreview);
    }

    private Optional<QaDocTestCasesPublicPreview> toPublicPreview(QaDocDocument document) {
        var testCases = QaDocContentParser.parseMarkedTestCases(document.getMarkdownContent());
        if (testCases.isEmpty()) {
            return Optional.empty();
        }

        String projectName = document.getProject() == null
                ? null
                : normalize(document.getProject().getName());
        String taskKey = normalize(document.getTaskId());

        return Optional.of(new QaDocTestCasesPublicPreview(
                "QA test cases",
                projectName,
                taskKey,
                findTaskSummary(document),
                testCases.stream()
                        .map(QaDocTestCaseResponse::fromTestCase)
                        .toList()
        ));
    }

    private String findTaskSummary(QaDocDocument document) {
        if (document.getLastAnalysisId() == null || document.getProject() == null
                || document.getProject().getId() == null) {
            return null;
        }

        Long documentProjectId = document.getProject().getId();
        String documentTaskKey = normalize(document.getTaskId());
        return codeAnalysisService.findById(document.getLastAnalysisId())
                .filter(analysis -> belongsToProject(analysis, documentProjectId))
                .filter(analysis -> hasCompatibleTaskKey(analysis, documentTaskKey))
                .map(CodeAnalysis::getTaskSummary)
                .map(QaDocTestCasesShareProvider::normalize)
                .orElse(null);
    }

    private boolean belongsToProject(CodeAnalysis analysis, Long projectId) {
        return analysis.getProject() != null
                && Objects.equals(analysis.getProject().getId(), projectId);
    }

    private boolean hasCompatibleTaskKey(CodeAnalysis analysis, String documentTaskKey) {
        String analysisTaskKey = normalize(analysis.getTaskId());
        return documentTaskKey == null || analysisTaskKey == null
                || Objects.equals(documentTaskKey, analysisTaskKey);
    }

    private static String normalize(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        return value.trim();
    }
}

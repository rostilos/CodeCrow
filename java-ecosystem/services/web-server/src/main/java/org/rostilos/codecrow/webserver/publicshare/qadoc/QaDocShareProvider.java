package org.rostilos.codecrow.webserver.publicshare.qadoc;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.qadoc.QaDocDocument;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.core.service.QaDocDocumentService;
import org.rostilos.codecrow.core.service.qadoc.QaDocContent;
import org.rostilos.codecrow.core.service.qadoc.QaDocContentParser;
import org.rostilos.codecrow.core.service.qadoc.QaDocPublicShareResource;
import org.rostilos.codecrow.security.service.UserDetailsImpl;
import org.rostilos.codecrow.security.web.WorkspaceSecurity;
import org.rostilos.codecrow.webserver.analysis.dto.response.QaDocTestCaseResponse;
import org.rostilos.codecrow.webserver.publicshare.PublicShareResourceProvider;
import org.springframework.security.core.Authentication;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.util.UriComponentsBuilder;

import java.util.Objects;
import java.util.Optional;

/** Resolves the explicitly shareable Overview, Test cases, and Environment QA tabs. */
@Component
public class QaDocShareProvider implements PublicShareResourceProvider {

    private final QaDocDocumentService qaDocDocumentService;
    private final CodeAnalysisService codeAnalysisService;
    private final WorkspaceSecurity workspaceSecurity;

    public QaDocShareProvider(QaDocDocumentService qaDocDocumentService,
                              CodeAnalysisService codeAnalysisService,
                              WorkspaceSecurity workspaceSecurity) {
        this.qaDocDocumentService = qaDocDocumentService;
        this.codeAnalysisService = codeAnalysisService;
        this.workspaceSecurity = workspaceSecurity;
    }

    @Override
    public String resourceType() {
        return QaDocPublicShareResource.DOCUMENT;
    }

    @Override
    @Transactional(readOnly = true)
    public Optional<QaDocPublicPreview> getPublicPreview(String resourceKey) {
        return parseDocumentId(resourceKey)
                .flatMap(qaDocDocumentService::findDocumentById)
                .flatMap(this::toPublicPreview);
    }

    @Override
    @Transactional(readOnly = true)
    public Optional<String> getAuthorizedPath(String resourceKey, Authentication authentication) {
        if (authentication == null || !authentication.isAuthenticated()
                || !(authentication.getPrincipal() instanceof UserDetailsImpl)) {
            return Optional.empty();
        }

        return parseDocumentId(resourceKey)
                .flatMap(qaDocDocumentService::findDocumentById)
                .flatMap(document -> authorizedDocumentPath(document, authentication));
    }

    private Optional<QaDocPublicPreview> toPublicPreview(QaDocDocument document) {
        if (!QaDocContentParser.hasCompleteShareableSections(document.getMarkdownContent())) {
            return Optional.empty();
        }
        var testCases = QaDocContentParser.parseMarkedTestCases(document.getMarkdownContent());
        QaDocContent content = QaDocContentParser.parse(document.getMarkdownContent());

        String projectName = document.getProject() == null
                ? null
                : normalize(document.getProject().getName());
        String taskKey = normalize(document.getTaskId());

        return Optional.of(new QaDocPublicPreview(
                "QA documentation",
                projectName,
                taskKey,
                findTaskSummary(document),
                content.overviewMarkdown(),
                testCases.stream()
                        .map(QaDocTestCaseResponse::fromTestCase)
                        .toList(),
                content.environmentMarkdown()
        ));
    }

    private Optional<String> authorizedDocumentPath(
            QaDocDocument document,
            Authentication authentication) {
        Project project = document.getProject();
        if (project == null || project.getId() == null || project.getWorkspace() == null
                || !workspaceSecurity.isProjectWorkspaceMember(project.getId(), authentication)) {
            return Optional.empty();
        }

        String workspaceSlug = normalize(project.getWorkspace().getSlug());
        String projectNamespace = normalize(project.getNamespace());
        if (workspaceSlug == null || projectNamespace == null || document.getPrNumber() == null) {
            return Optional.empty();
        }

        return Optional.of(UriComponentsBuilder
                .fromPath("/dashboard/{workspaceSlug}/projects/{projectNamespace}")
                .queryParam("prNumber", document.getPrNumber())
                .queryParam("subTab", "qa-doc")
                .buildAndExpand(workspaceSlug, projectNamespace)
                .encode()
                .toUriString());
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
                .map(QaDocShareProvider::normalize)
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

    private static Optional<Long> parseDocumentId(String resourceKey) {
        try {
            return Optional.of(Long.valueOf(resourceKey));
        } catch (NumberFormatException ignored) {
            return Optional.empty();
        }
    }
}

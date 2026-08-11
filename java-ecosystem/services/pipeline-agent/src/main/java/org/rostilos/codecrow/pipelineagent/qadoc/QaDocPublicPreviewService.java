package org.rostilos.codecrow.pipelineagent.qadoc;

import org.rostilos.codecrow.core.model.qadoc.QaDocDocument;
import org.rostilos.codecrow.core.service.SiteSettingsProvider;
import org.rostilos.codecrow.core.service.qadoc.QaDocContentParser;
import org.rostilos.codecrow.core.service.qadoc.QaDocPublicShareResource;
import org.rostilos.codecrow.publicshare.api.IssuedPublicShare;
import org.rostilos.codecrow.publicshare.service.PublicShareLinkService;
import org.springframework.stereotype.Service;

/** QA-doc adapter over the purpose-neutral public-share credential service. */
@Service
public class QaDocPublicPreviewService {

    private final PublicShareLinkService publicShareLinkService;
    private final SiteSettingsProvider siteSettingsProvider;

    public QaDocPublicPreviewService(PublicShareLinkService publicShareLinkService,
                                     SiteSettingsProvider siteSettingsProvider) {
        this.publicShareLinkService = publicShareLinkService;
        this.siteSettingsProvider = siteSettingsProvider;
    }

    public String createTestCasesPreviewUrl(QaDocDocument document) {
        if (document == null || document.getId() == null) {
            throw new IllegalArgumentException("A persisted QA document is required for public preview.");
        }
        if (QaDocContentParser.parseMarkedTestCases(document.getMarkdownContent()).isEmpty()) {
            throw new IllegalArgumentException(
                    "A marked QA test-case section is required for public preview.");
        }
        IssuedPublicShare share = publicShareLinkService.issue(
                QaDocPublicShareResource.TEST_CASES,
                document.getId().toString()
        );
        return share.toFrontendUrl(siteSettingsProvider.getBaseUrlSettings().frontendUrl());
    }

    public String buildTaskComment(String qaDocument, String previewUrl) {
        if (previewUrl == null || previewUrl.isBlank()) {
            throw new IllegalArgumentException("A public preview URL is required.");
        }
        return QaAutoDocListener.COMMENT_MARKER
                + "\n\n"
                + QaDocContentParser.replaceMarkedTestCases(qaDocument, previewUrl.trim());
    }
}

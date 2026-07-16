package org.rostilos.codecrow.analysisengine.delivery;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Set;

import org.junit.jupiter.api.Test;

class ReviewProviderEffectIdentityTest {
    private static final long TENANT_ID = 7L;
    private static final String PROVIDER = "github";
    private static final String REPOSITORY = "github:octo/codecrow";
    private static final long PULL_REQUEST_ID = 42L;
    private static final String HEAD = "a".repeat(40);
    private static final String REPORT_DIGEST = "b".repeat(64);
    private static final String PUBLICATION_KIND = "ANALYSIS_RESULTS";

    @Test
    void derivesOneDeterministicNormalizedShaForTheSameProviderEffect() {
        String canonical = derive(
                TENANT_ID,
                PROVIDER,
                REPOSITORY,
                PULL_REQUEST_ID,
                HEAD,
                REPORT_DIGEST,
                PUBLICATION_KIND);

        assertThat(canonical).matches("[0-9a-f]{64}");
        assertThat(derive(
                TENANT_ID,
                "GITHUB",
                REPOSITORY,
                PULL_REQUEST_ID,
                HEAD.toUpperCase(),
                REPORT_DIGEST,
                PUBLICATION_KIND)).isEqualTo(canonical);
        assertThat(derive(
                TENANT_ID,
                PROVIDER,
                REPOSITORY,
                PULL_REQUEST_ID,
                HEAD,
                REPORT_DIGEST,
                PUBLICATION_KIND)).isEqualTo(canonical);
    }

    @Test
    void everyProviderVisibleCoordinateIndependentlyChangesTheEffectIdentity() {
        String baseline = derive(
                TENANT_ID,
                PROVIDER,
                REPOSITORY,
                PULL_REQUEST_ID,
                HEAD,
                REPORT_DIGEST,
                PUBLICATION_KIND);

        Set<String> variants = Set.of(
                derive(8L, PROVIDER, REPOSITORY, PULL_REQUEST_ID, HEAD,
                        REPORT_DIGEST, PUBLICATION_KIND),
                derive(TENANT_ID, "gitlab", REPOSITORY, PULL_REQUEST_ID, HEAD,
                        REPORT_DIGEST, PUBLICATION_KIND),
                derive(TENANT_ID, PROVIDER, "github:octo/other", PULL_REQUEST_ID, HEAD,
                        REPORT_DIGEST, PUBLICATION_KIND),
                derive(TENANT_ID, PROVIDER, REPOSITORY, 43L, HEAD,
                        REPORT_DIGEST, PUBLICATION_KIND),
                derive(TENANT_ID, PROVIDER, REPOSITORY, PULL_REQUEST_ID,
                        "c".repeat(40), REPORT_DIGEST, PUBLICATION_KIND),
                derive(TENANT_ID, PROVIDER, REPOSITORY, PULL_REQUEST_ID, HEAD,
                        "d".repeat(64), PUBLICATION_KIND),
                derive(TENANT_ID, PROVIDER, REPOSITORY, PULL_REQUEST_ID, HEAD,
                        REPORT_DIGEST, "INLINE_COMMENTS"));

        assertThat(variants).hasSize(7).doesNotContain(baseline);
    }

    private static String derive(
            long tenantId,
            String provider,
            String repositoryId,
            long pullRequestId,
            String headRevision,
            String reportDigest,
            String publicationKind) {
        return ReviewProviderEffectIdentity.derive(
                tenantId,
                provider,
                repositoryId,
                pullRequestId,
                headRevision,
                reportDigest,
                publicationKind);
    }
}

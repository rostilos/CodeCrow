# Java legacy integration-test inventory

`java-legacy-it-inventory-v1.json` is the authoritative, fail-closed inventory for every Java file below a legacy `src/it/java` tree. It records 37 files: 4 support types, 11 concrete local-double selectors, 20 concrete container-backed selectors, and 2 abstract bases. All sources contain 244 literal `@Test` tokens in total. This drift sentinel intentionally counts prefixes such as `@TestMethodOrder` and `@Testcontainers`. The executable contract separately records 228 exact `@Test` method annotations: 65 local-double and 163 container-backed.

## Canonical local-double lane

The workflow constructs a same-checkout Maven cache in the authorized dependency phase, binds its canonical receipt SHA-256 through the job output, freezes it read-only, and revalidates both the cache manifest and POM inventory before every P0-07 Java command. The Java quality reactor runs unit tests first, then exactly the local-double inventory through Failsafe:

```bash
export CODECROW_MAVEN_REPOSITORY="$GITHUB_WORKSPACE/.llm-handoff-artifacts/p0-07/dependency-cache/maven"
export CODECROW_P007_CACHE_RECEIPT_SHA256='<sha256 from the cache-freeze step>'
SAFE_LEGACY_ITS='org.rostilos.codecrow.analysisengine.AiClientIT,org.rostilos.codecrow.email.EmailDeliveryIT,org.rostilos.codecrow.email.service.TemplateRenderingIT,org.rostilos.codecrow.ragengine.RagPipelineClientIT,org.rostilos.codecrow.security.JwtValidationIT,org.rostilos.codecrow.security.TokenEncryptionIT,org.rostilos.codecrow.vcsclient.BitbucketClientIT,org.rostilos.codecrow.vcsclient.GitHubClientIT,org.rostilos.codecrow.vcsclient.GitLabClientIT,org.rostilos.codecrow.vcsclient.VcsClientErrorHandlingIT,org.rostilos.codecrow.vcsclient.refresh.TokenRefreshIT'
CODECROW_EXTERNAL_CALL_LEDGER_DIR="$GITHUB_WORKSPACE/.llm-handoff-artifacts/p0-03/test-ledgers/p0-07-java-quality-ci" \
  tools/quality-gates/bin/run-java-coverage-offline.sh \
  mvn -f java-ecosystem/pom.xml \
  -s tools/offline-harness/maven/settings-ci.xml \
  -o -B --no-transfer-progress \
  -Pquality-coverage,p007-integration-only \
  -pl libs/vcs-client,libs/security,libs/email,libs/analysis-engine,libs/rag-engine \
  "-Dit.test=$SAFE_LEGACY_ITS" \
  verify
```

The workflow must statically prove that the selector CSV is exactly the 11 `localDouble` classes and contains none of the 20 `containerBacked` classes. It must delete stale Failsafe reports before execution, then reconcile JUnit XML by exact module and class: 11 unique reports/classes, 65 tests, zero failures/errors/skips, no extras, duplicates, or stale reports. It must also retain those reports and validate the external-call ledger.

## Guarded container-backed lanes

The 20 container-backed selectors remain fail-closed outside `run-java-legacy-it-guarded.sh`. That wrapper divides them into queue, pipeline, and web lanes and supplies the only reviewed activation contract. `java-legacy-it-container-quarantine-v1.json` retains its historical filename but now records the guarded-only policy and its expiry.

Every guarded lane enforces all of the following:

1. Admit only the digest-pinned PostgreSQL and Redis references from `persistence-images-v1.json` and verify that manifest's pinned SHA-256. Qdrant is outside this legacy lane and must not be admitted or started.
2. Allocate a fresh task namespace and record its receipt.
3. enforce a `NEVER` pull policy and prove zero runtime image pulls with Docker event evidence.
4. Record the external-call ledger and every task-owned container ID.
5. Tear down only task-owned resources and prove exact container absence afterward.

Across the three lanes, the validator reconciles exactly 20 unique classes and 163 tests with zero failures, errors, or skips and no extra, duplicate, or stale XML.

The unguarded workflow never places a `containerBacked` selector in its Failsafe selector property; each guarded profile pins its own exact selector census and target artifact.

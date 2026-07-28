package org.rostilos.codecrow.pipelineagent.generic.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.OkHttpClient;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.FileContentDto;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.service.pr.PrFileEnrichmentService;
import org.rostilos.codecrow.analysisengine.service.pr.PullRequestDiffPreparationService;
import org.rostilos.codecrow.analysisengine.util.AnalysisLimitEnforcer;
import org.rostilos.codecrow.analysisengine.util.PromptDryRunMode;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectAiConnectionBinding;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.plugins.PluginRuntime;
import org.rostilos.codecrow.queue.RedisQueueService;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.client.RestTemplate;

import java.net.URL;
import java.net.URLClassLoader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.GeneralSecurityException;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Test-side producer used by the deployed neutral replay.
 *
 * <p>The fixture has no provider endpoint or repository remote. It drives the
 * production neutral acquisition/request builder and the production Redis
 * serializer, then writes the captured queue envelope only when the replay
 * supplies explicit input/output paths.</p>
 */
class IsolatedReviewProducerReplayTest {
    private static final String FIXTURE_PATH_PROPERTY =
            "reviewQuality.syntheticFixture";
    private static final String OUTPUT_PATH_PROPERTY =
            "reviewQuality.queueEnvelopeOutput";
    private static final String PLUGIN_DIRECTORY_PROPERTY =
            "reviewQuality.pluginDirectory";
    private static final long PROJECT_ID = 900001L;
    private static final long PULL_REQUEST_ID = 42L;
    private static final String TARGET_BRANCH = "main";
    private static final String SOURCE_BRANCH = "feature/neutral-context";

    @AfterEach
    void clearDryRunProperties() {
        System.clearProperty(PromptDryRunMode.ENABLED_KEY);
        System.clearProperty(PromptDryRunMode.PROJECT_IDS_KEY);
    }

    @Test
    void emitsProductionQueueEnvelopeForSyntheticImmutableSnapshot()
            throws Exception {
        String fixturePath = System.getProperty(FIXTURE_PATH_PROPERTY);
        String outputPath = System.getProperty(OUTPUT_PATH_PROPERTY);
        String pluginDirectory = System.getProperty(PLUGIN_DIRECTORY_PROPERTY);
        if (fixturePath == null && outputPath == null && pluginDirectory == null) {
            return;
        }
        assertThat(fixturePath).isNotBlank();
        assertThat(outputPath).isNotBlank();
        assertThat(pluginDirectory).isNotBlank();

        ObjectMapper objectMapper = new ObjectMapper();
        JsonNode fixture = objectMapper.readTree(Path.of(fixturePath).toFile());
        String baseRevision = requiredText(fixture, "baseRevision");
        String headRevision = requiredText(fixture, "headRevision");
        String rawDiff = requiredText(fixture, "rawDiff");
        String projectNamespace = requiredText(fixture, "projectNamespace");
        List<String> expectedRepositoryPlugins = objectMapper.convertValue(
                fixture.path("expectedRepositoryPlugins"),
                objectMapper.getTypeFactory().constructCollectionType(
                        List.class, String.class));
        Map<String, String> headFiles = objectMapper.convertValue(
                fixture.path("headFiles"),
                objectMapper.getTypeFactory().constructMapType(
                        Map.class, String.class, String.class));

        Project project = project(projectNamespace);
        PrProcessRequest processRequest = processRequest(headRevision);
        VcsClient vcsClient = syntheticVcsClient(headFiles, headRevision);
        OkHttpClient httpClient = new OkHttpClient();
        VcsClientProvider vcsClientProvider =
                new SyntheticVcsClientProvider(httpClient, vcsClient);
        PrFileEnrichmentService enrichmentService =
                new SyntheticEnrichmentService(headFiles);
        TokenEncryptionService encryptionService =
                new SyntheticTokenEncryptionService();

        try (URLClassLoader pluginLoader = pluginLoader(
                Path.of(pluginDirectory))) {
            ProjectCapabilitySelectionService capabilitySelection =
                    new ProjectCapabilitySelectionService(
                            PluginRuntime.discover(pluginLoader));
            SyntheticAiClientService producer = new SyntheticAiClientService(
                    encryptionService,
                    vcsClientProvider,
                    enrichmentService,
                    capabilitySelection,
                    new PullRequestDiffPreparationService(
                            new AnalysisLimitEnforcer()),
                    baseRevision,
                    headRevision,
                    rawDiff);

            List<AiAnalysisRequest> requests = producer.buildAiAnalysisRequests(
                    project,
                    processRequest,
                    Optional.empty());
            assertThat(requests).hasSize(1);
            AiAnalysisRequest reviewRequest = requests.get(0);
            assertThat(project.getEffectiveConfig().mainBranch())
                    .isEqualTo(TARGET_BRANCH);
            assertThat(reviewRequest.getTargetBranchName())
                    .isEqualTo(project.getEffectiveConfig().mainBranch());
            assertThat(reviewRequest.getSourceBranchName())
                    .isEqualTo(SOURCE_BRANCH);
            assertThat(reviewRequest.getBaseCommitHash())
                    .isEqualTo(baseRevision);
            assertThat(reviewRequest.getCurrentCommitHash())
                    .isEqualTo(headRevision);
            assertThat(reviewRequest.getRagEnabled()).isFalse();
            assertThat(reviewRequest.getChangedFiles())
                    .containsExactlyElementsOf(new ArrayList<>(headFiles.keySet()));
            assertThat(reviewRequest.getProjectCapabilities())
                    .isNotNull();
            assertThat(reviewRequest.getProjectCapabilities()
                    .repositoryPlugins())
                    .containsExactlyElementsOf(expectedRepositoryPlugins);

            RedisQueueService queueService = new SyntheticQueueService(
                    objectMapper.writeValueAsString(Map.of(
                            "type", "final",
                            "result", Map.of(
                                    "dryRun", true,
                                    "status", "prompt_capture_completed"))));
            System.setProperty(PromptDryRunMode.ENABLED_KEY, "true");
            System.setProperty(
                    PromptDryRunMode.PROJECT_IDS_KEY,
                    String.valueOf(PROJECT_ID));

            AiAnalysisClient queueProducer = new AiAnalysisClient(
                    new RestTemplate(), queueService, objectMapper);
            queueProducer.performAnalysis(reviewRequest);

            String envelope = ((SyntheticQueueService) queueService)
                    .capturedEnvelope();
            JsonNode queued = objectMapper.readTree(envelope);
            JsonNode queuedRequest = queued.path("request");
            assertThat(queuedRequest.path("promptDryRun").asBoolean()).isTrue();
            assertThat(queuedRequest.path("targetBranchName").asText())
                    .isEqualTo(TARGET_BRANCH);
            assertThat(queuedRequest.path("sourceBranchName").asText())
                    .isEqualTo(SOURCE_BRANCH);
            assertThat(queuedRequest.path("baseCommitHash").asText())
                    .isEqualTo(baseRevision);
            assertThat(queuedRequest.path("currentCommitHash").asText())
                    .isEqualTo(headRevision);
            assertThat(queuedRequest.path("ragEnabled").asBoolean()).isFalse();
            assertThat(queuedRequest.path("aiApiKey").asText())
                    .isEqualTo("dry-run-provider-disabled");
            assertThat(queuedRequest.path("accessToken").isNull()).isTrue();

            Path output = Path.of(outputPath);
            Files.createDirectories(output.toAbsolutePath().getParent());
            Files.writeString(
                    output,
                    objectMapper.writerWithDefaultPrettyPrinter()
                            .writeValueAsString(queued) + System.lineSeparator());
        }
    }

    private static Project project(String namespace) {
        Project project = new Project();
        ReflectionTestUtils.setField(project, "id", PROJECT_ID);
        project.setName("Isolated neutral review fixture");
        project.setNamespace(namespace);
        project.setConfiguration(new ProjectConfig(false, TARGET_BRANCH));

        Workspace workspace = new Workspace(
                "codecrow-quality-isolated",
                "codecrow-quality-isolated",
                "disconnected review-quality fixture");
        project.setWorkspace(workspace);

        AIConnection aiConnection = new AIConnection();
        aiConnection.setProviderKey(AIProviderKey.OPENAI);
        aiConnection.setAiModel("provider-model-never-constructed");
        aiConnection.setApiKeyEncrypted("encrypted-review-key");
        ProjectAiConnectionBinding aiBinding =
                new ProjectAiConnectionBinding();
        aiBinding.setProject(project);
        aiBinding.setAiConnection(aiConnection);
        project.setAiConnectionBinding(aiBinding);

        VcsConnection vcsConnection = new VcsConnection();
        vcsConnection.setProviderType(EVcsProvider.GITHUB);
        vcsConnection.setConnectionType(EVcsConnectionType.ACCESS_TOKEN);
        vcsConnection.setAccessToken("encrypted-vcs-token");
        VcsRepoBinding vcsBinding = new VcsRepoBinding();
        vcsBinding.setProject(project);
        vcsBinding.setWorkspace(workspace);
        vcsBinding.setVcsConnection(vcsConnection);
        vcsBinding.setProvider(EVcsProvider.GITHUB);
        vcsBinding.setExternalRepoId("synthetic-local-repository");
        vcsBinding.setExternalNamespace("codecrow-quality-isolated");
        vcsBinding.setExternalRepoSlug(namespace);
        vcsBinding.setDefaultBranch(TARGET_BRANCH);
        project.setVcsRepoBinding(vcsBinding);
        return project;
    }

    private static PrProcessRequest processRequest(String headRevision) {
        PrProcessRequest request = new PrProcessRequest();
        request.projectId = PROJECT_ID;
        request.pullRequestId = PULL_REQUEST_ID;
        request.targetBranchName = TARGET_BRANCH;
        request.sourceBranchName = SOURCE_BRANCH;
        request.commitHash = headRevision;
        request.analysisType = AnalysisType.PR_REVIEW;
        return request;
    }

    private static PrEnrichmentDataDto enrichment(
            List<String> requestedPaths,
            Map<String, String> headFiles) {
        List<FileContentDto> contents = requestedPaths.stream()
                .map(path -> FileContentDto.of(path, headFiles.get(path)))
                .toList();
        long bytes = contents.stream()
                .mapToLong(FileContentDto::sizeBytes)
                .sum();
        return new PrEnrichmentDataDto(
                contents,
                List.of(),
                List.of(),
                new PrEnrichmentDataDto.EnrichmentStats(
                        requestedPaths.size(),
                        requestedPaths.size(),
                        0,
                        0,
                        bytes,
                        0,
                        Map.of()));
    }

    private static VcsClient syntheticVcsClient(
            Map<String, String> headFiles,
            String headRevision) {
        return (VcsClient) java.lang.reflect.Proxy.newProxyInstance(
                IsolatedReviewProducerReplayTest.class.getClassLoader(),
                new Class<?>[]{VcsClient.class},
                (proxy, method, arguments) -> {
                    if (method.getDeclaringClass() == Object.class) {
                        return switch (method.getName()) {
                            case "toString" -> "DisconnectedSyntheticVcsClient";
                            case "hashCode" -> System.identityHashCode(proxy);
                            case "equals" -> proxy == arguments[0];
                            default -> throw new UnsupportedOperationException(
                                    method.getName());
                        };
                    }
                    if ("getFileContent".equals(method.getName())) {
                        String path = (String) arguments[2];
                        String revision = (String) arguments[3];
                        if (!headRevision.equals(revision)) {
                            throw new IllegalArgumentException(
                                    "unexpected synthetic revision: "
                                            + revision);
                        }
                        return headFiles.get(path);
                    }
                    throw new UnsupportedOperationException(
                            "unexpected synthetic VCS operation: "
                                    + method.getName());
                });
    }

    private static final class SyntheticVcsClientProvider
            extends VcsClientProvider {
        private final OkHttpClient httpClient;
        private final VcsClient vcsClient;

        private SyntheticVcsClientProvider(
                OkHttpClient httpClient,
                VcsClient vcsClient) {
            super(null, null, null, null, null);
            this.httpClient = httpClient;
            this.vcsClient = vcsClient;
        }

        @Override
        public OkHttpClient getHttpClient(VcsConnection connection) {
            return httpClient;
        }

        @Override
        public VcsClient getClient(VcsConnection connection) {
            return vcsClient;
        }
    }

    private static final class SyntheticEnrichmentService
            extends PrFileEnrichmentService {
        private final Map<String, String> headFiles;

        private SyntheticEnrichmentService(Map<String, String> headFiles) {
            this.headFiles = headFiles;
        }

        @Override
        public boolean isEnrichmentEnabled() {
            return false;
        }

        @Override
        public PrEnrichmentDataDto fetchFileContentsOnly(
                VcsClient vcsClient,
                String workspace,
                String repoSlug,
                String branchOrCommit,
                List<String> changedFiles) {
            return enrichment(changedFiles, headFiles);
        }
    }

    private static final class SyntheticTokenEncryptionService
            extends TokenEncryptionService {
        private static final String UNUSED_KEY = Base64.getEncoder()
                .encodeToString(new byte[32]);

        private SyntheticTokenEncryptionService() {
            super(UNUSED_KEY, null);
        }

        @Override
        public String decrypt(String encrypted)
                throws GeneralSecurityException {
            return switch (encrypted) {
                case "encrypted-review-key" ->
                        "review-key-must-be-disabled";
                case "encrypted-vcs-token" -> "synthetic-vcs-token";
                default -> throw new GeneralSecurityException(
                        "unexpected synthetic encrypted value");
            };
        }
    }

    private static final class SyntheticQueueService
            extends RedisQueueService {
        private final AtomicReference<String> envelope =
                new AtomicReference<>();
        private final String finalResponse;

        private SyntheticQueueService(String finalResponse) {
            super(null);
            this.finalResponse = finalResponse;
        }

        @Override
        public void leftPush(String queueKey, String payload) {
            if (!"codecrow:analysis:jobs".equals(queueKey)) {
                throw new IllegalArgumentException(
                        "unexpected synthetic queue: " + queueKey);
            }
            if (!envelope.compareAndSet(null, payload)) {
                throw new IllegalStateException(
                        "synthetic producer emitted multiple queue envelopes");
            }
        }

        @Override
        public String rightPop(String queueKey, long timeoutSeconds) {
            return finalResponse;
        }

        @Override
        public void setExpiry(String key, long timeoutMinutes) {
            // The disconnected producer has no Redis state to expire.
        }

        @Override
        public void deleteKey(String key) {
            // The disconnected producer has no Redis state to delete.
        }

        private String capturedEnvelope() {
            String value = envelope.get();
            if (value == null) {
                throw new IllegalStateException(
                        "synthetic producer emitted no queue envelope");
            }
            return value;
        }
    }

    private static URLClassLoader pluginLoader(Path directory)
            throws Exception {
        List<Path> jars;
        try (var paths = Files.list(directory)) {
            jars = paths
                    .filter(Files::isRegularFile)
                    .filter(path -> path.getFileName().toString()
                            .startsWith("codecrow-plugin-"))
                    .filter(path -> path.getFileName().toString()
                            .endsWith(".jar"))
                    .sorted(Comparator.comparing(
                            path -> path.getFileName().toString()))
                    .toList();
        }
        URL[] urls = jars.stream()
                .map(path -> {
                    try {
                        return path.toUri().toURL();
                    } catch (Exception error) {
                        throw new IllegalStateException(error);
                    }
                })
                .toArray(URL[]::new);
        return new URLClassLoader(
                urls,
                IsolatedReviewProducerReplayTest.class.getClassLoader());
    }

    private static String requiredText(JsonNode fixture, String field) {
        String value = fixture.path(field).asText(null);
        assertThat(value).isNotBlank();
        return value;
    }

    private static final class SyntheticAiClientService
            extends AbstractVcsAiClientService {
        private final String baseRevision;
        private final String headRevision;
        private final String rawDiff;

        private SyntheticAiClientService(
                TokenEncryptionService encryptionService,
                VcsClientProvider vcsClientProvider,
                PrFileEnrichmentService enrichmentService,
                ProjectCapabilitySelectionService capabilitySelection,
                PullRequestDiffPreparationService diffPreparationService,
                String baseRevision,
                String headRevision,
                String rawDiff) {
            super(
                    encryptionService,
                    vcsClientProvider,
                    enrichmentService,
                    null,
                    null,
                    capabilitySelection,
                    diffPreparationService);
            this.baseRevision = baseRevision;
            this.headRevision = headRevision;
            this.rawDiff = rawDiff;
        }

        @Override
        protected PullRequestData fetchPullRequest(
                OkHttpClient client,
                RepositoryInfo repository,
                long pullRequestId) {
            return pullRequestData(
                    "Isolated neutral mixed-language context replay",
                    "Synthetic immutable snapshot with no repository remote",
                    SOURCE_BRANCH,
                    TARGET_BRANCH,
                    baseRevision,
                    headRevision);
        }

        @Override
        protected String fetchCommitRangeDiff(
                OkHttpClient client,
                RepositoryInfo repository,
                String baseCommit,
                String headCommit) {
            assertThat(baseCommit).isEqualTo(baseRevision);
            assertThat(headCommit).isEqualTo(headRevision);
            return rawDiff;
        }

        @Override
        public EVcsProvider getProvider() {
            return EVcsProvider.GITHUB;
        }
    }
}

package org.rostilos.codecrow.mcp.generic;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.InvalidPathException;
import java.nio.file.LinkOption;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/**
 * Provider-neutral VCS client adapter that serves one pinned repository tree
 * from disk while delegating provider operations to the normal remote client.
 */
public final class LocalRepoClient implements VcsMcpClient {
    private static final String REVIEW_OVERLAY_MANIFEST = "manifest.json";
    private static final String REVIEW_OVERLAY_FILES = "files";
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    private volatile VcsMcpClient remoteClient;
    private final RemoteClientProvider remoteClientProvider;
    private final Path repoRoot;
    private final Path realRepoRoot;
    private final String expectedWorkspace;
    private final String expectedRepoSlug;
    private final String targetBranch;
    private final String targetRevision;
    private final ReviewOverlay reviewOverlay;
    private final String reviewOverlayFailure;

    public LocalRepoClient(
            VcsMcpClient remoteClient,
            String repoRootPath,
            String expectedWorkspace,
            String expectedRepoSlug,
            String targetBranch,
            String targetRevision
    ) throws IOException {
        this(
                () -> remoteClient,
                repoRootPath,
                expectedWorkspace,
                expectedRepoSlug,
                targetBranch,
                targetRevision,
                null);
        this.remoteClient = Objects.requireNonNull(remoteClient, "remoteClient");
    }

    public LocalRepoClient(
            VcsMcpClient remoteClient,
            String repoRootPath,
            String expectedWorkspace,
            String expectedRepoSlug,
            String targetBranch,
            String targetRevision,
            String reviewOverlayPath
    ) throws IOException {
        this(
                () -> remoteClient,
                repoRootPath,
                expectedWorkspace,
                expectedRepoSlug,
                targetBranch,
                targetRevision,
                reviewOverlayPath);
        this.remoteClient = Objects.requireNonNull(remoteClient, "remoteClient");
    }

    public LocalRepoClient(
            RemoteClientProvider remoteClientProvider,
            String repoRootPath,
            String expectedWorkspace,
            String expectedRepoSlug,
            String targetBranch,
            String targetRevision,
            String reviewOverlayPath
    ) throws IOException {
        this.remoteClientProvider = Objects.requireNonNull(
                remoteClientProvider, "remoteClientProvider");
        this.repoRoot = Path.of(repoRootPath).toAbsolutePath().normalize();
        if (!Files.isDirectory(repoRoot)) {
            throw new IOException("Local repository path is not a directory: " + repoRoot);
        }
        this.realRepoRoot = repoRoot.toRealPath();
        this.expectedWorkspace = Objects.requireNonNull(
                expectedWorkspace, "expectedWorkspace");
        this.expectedRepoSlug = Objects.requireNonNull(
                expectedRepoSlug, "expectedRepoSlug");
        this.targetBranch = targetBranch;
        this.targetRevision = targetRevision;
        ReviewOverlay resolvedOverlay = null;
        String overlayFailure = null;
        if (reviewOverlayPath != null && !reviewOverlayPath.isBlank()) {
            try {
                resolvedOverlay = ReviewOverlay.open(reviewOverlayPath);
            } catch (IOException | RuntimeException failure) {
                overlayFailure = failure.getMessage();
            }
        }
        this.reviewOverlay = resolvedOverlay;
        this.reviewOverlayFailure = overlayFailure;
    }

    private VcsMcpClient providerClient() throws IOException {
        VcsMcpClient client = remoteClient;
        if (client == null) {
            synchronized (this) {
                client = remoteClient;
                if (client == null) {
                    client = Objects.requireNonNull(
                            remoteClientProvider.get(), "remoteClientProvider result");
                    remoteClient = client;
                }
            }
        }
        return client;
    }

    @FunctionalInterface
    public interface RemoteClientProvider {
        VcsMcpClient get() throws IOException;
    }

    private boolean isRequestRepository(String workspace, String repoSlug) {
        return expectedWorkspace.equals(workspace)
                && expectedRepoSlug.equals(repoSlug);
    }

    private boolean isLocalRef(
            String workspace,
            String repoSlug,
            String branchOrRevision
    ) {
        if (branchOrRevision == null || branchOrRevision.isBlank()) {
            return false;
        }
        return isRequestRepository(workspace, repoSlug)
                && (branchOrRevision.equals(targetBranch)
                || branchOrRevision.equals(targetRevision));
    }

    private Path resolveExistingPath(String relativePath) throws IOException {
        String path = relativePath == null ? "" : relativePath;
        Path candidate = repoRoot.resolve(path).normalize();
        if (!candidate.startsWith(repoRoot)) {
            throw new IOException("Repository path escapes the local snapshot: " + path);
        }
        if (!Files.exists(candidate, LinkOption.NOFOLLOW_LINKS)) {
            throw new NoSuchFileException(path);
        }
        Path realCandidate = candidate.toRealPath();
        if (!realCandidate.startsWith(realRepoRoot)) {
            throw new IOException("Repository path escapes the local snapshot: " + path);
        }
        return realCandidate;
    }

    private String readDirectory(String dirPath) throws IOException {
        Path directory = resolveExistingPath(dirPath);
        if (!Files.isDirectory(directory)) {
            throw new IOException("Repository path is not a directory: " + dirPath);
        }
        try (var entries = Files.list(directory)) {
            return String.join("\n", entries
                    .sorted((left, right) -> left.getFileName().toString()
                            .compareTo(right.getFileName().toString()))
                    .map(entry -> entry.getFileName().toString()
                            + (Files.isDirectory(entry, LinkOption.NOFOLLOW_LINKS) ? "/" : ""))
                    .toList());
        }
    }

    private String readLocalFile(String filePath) throws IOException {
        Path file = resolveExistingPath(filePath);
        if (!Files.isRegularFile(file)) {
            throw new IOException("Repository path is not a file: " + filePath);
        }
        return Files.readString(file, StandardCharsets.UTF_8);
    }

    @Override
    public String getProviderType() {
        VcsMcpClient client = remoteClient;
        return client != null
                ? client.getProviderType()
                : System.getProperty("vcs.provider", "local");
    }

    @Override
    public String getPrNumber() throws IOException {
        return providerClient().getPrNumber();
    }

    @Override
    public String getPullRequestTitle() throws IOException {
        return providerClient().getPullRequestTitle();
    }

    @Override
    public String getPullRequestDescription() throws IOException {
        return providerClient().getPullRequestDescription();
    }

    @Override
    public List<FileDiffInfo> getPullRequestChanges() throws IOException {
        return providerClient().getPullRequestChanges();
    }

    @Override
    public List<Map<String, Object>> listRepositories(String workspace, Integer limit) throws IOException {
        return providerClient().listRepositories(workspace, limit);
    }

    @Override
    public Map<String, Object> getRepository(String workspace, String repoSlug) throws IOException {
        return providerClient().getRepository(workspace, repoSlug);
    }

    @Override
    public List<Map<String, Object>> getPullRequests(
            String workspace,
            String repoSlug,
            String state,
            Integer limit
    ) throws IOException {
        return providerClient().getPullRequests(workspace, repoSlug, state, limit);
    }

    @Override
    public Map<String, Object> createPullRequest(
            String workspace,
            String repoSlug,
            String title,
            String description,
            String sourceBranch,
            String targetBranch,
            List<String> reviewers
    ) throws IOException {
        return providerClient().createPullRequest(
                workspace, repoSlug, title, description, sourceBranch, targetBranch, reviewers);
    }

    @Override
    public Map<String, Object> getPullRequest(
            String workspace,
            String repoSlug,
            String pullRequestId
    ) throws IOException {
        return providerClient().getPullRequest(workspace, repoSlug, pullRequestId);
    }

    @Override
    public Map<String, Object> updatePullRequest(
            String workspace,
            String repoSlug,
            String pullRequestId,
            String title,
            String description
    ) throws IOException {
        return providerClient().updatePullRequest(
                workspace, repoSlug, pullRequestId, title, description);
    }

    @Override
    public Object getPullRequestActivity(
            String workspace,
            String repoSlug,
            String pullRequestId
    ) throws IOException {
        return providerClient().getPullRequestActivity(workspace, repoSlug, pullRequestId);
    }

    @Override
    public Object approvePullRequest(
            String workspace,
            String repoSlug,
            String pullRequestId
    ) throws IOException {
        return providerClient().approvePullRequest(workspace, repoSlug, pullRequestId);
    }

    @Override
    public Object unapprovePullRequest(
            String workspace,
            String repoSlug,
            String pullRequestId
    ) throws IOException {
        return providerClient().unapprovePullRequest(workspace, repoSlug, pullRequestId);
    }

    @Override
    public Object declinePullRequest(
            String workspace,
            String repoSlug,
            String pullRequestId,
            String message
    ) throws IOException {
        return providerClient().declinePullRequest(
                workspace, repoSlug, pullRequestId, message);
    }

    @Override
    public Object mergePullRequest(
            String workspace,
            String repoSlug,
            String pullRequestId,
            String message,
            String strategy
    ) throws IOException {
        return providerClient().mergePullRequest(
                workspace, repoSlug, pullRequestId, message, strategy);
    }

    @Override
    public Object getPullRequestComments(
            String workspace,
            String repoSlug,
            String pullRequestId
    ) throws IOException {
        return providerClient().getPullRequestComments(workspace, repoSlug, pullRequestId);
    }

    @Override
    public String getPullRequestDiff(
            String workspace,
            String repoSlug,
            String pullRequestId
    ) throws IOException {
        return providerClient().getPullRequestDiff(workspace, repoSlug, pullRequestId);
    }

    @Override
    public Object getPullRequestCommits(
            String workspace,
            String repoSlug,
            String pullRequestId
    ) throws IOException {
        return providerClient().getPullRequestCommits(workspace, repoSlug, pullRequestId);
    }

    @Override
    public Map<String, Object> getBranchingModel(String workspace, String repoSlug) throws IOException {
        return providerClient().getBranchingModel(workspace, repoSlug);
    }

    @Override
    public Map<String, Object> getBranchingModelSettings(String workspace, String repoSlug) throws IOException {
        return providerClient().getBranchingModelSettings(workspace, repoSlug);
    }

    @Override
    public Map<String, Object> updateBranchingModelSettings(
            String workspace,
            String repoSlug,
            Map<String, Object> development,
            Map<String, Object> production,
            List<Map<String, Object>> branchTypes
    ) throws IOException {
        return providerClient().updateBranchingModelSettings(
                workspace, repoSlug, development, production, branchTypes);
    }

    @Override
    public String getBranchFileContent(
            String workspace,
            String repoSlug,
            String branch,
            String filePath
    ) throws IOException {
        if (!isLocalRef(workspace, repoSlug, branch)) {
            return providerClient().getBranchFileContent(workspace, repoSlug, branch, filePath);
        }
        try {
            return readLocalFile(filePath);
        } catch (NoSuchFileException absentFromSnapshot) {
            return providerClient().getBranchFileContent(
                    workspace, repoSlug, targetFallbackRef(), filePath);
        }
    }

    /**
     * Reads the proposed PR tree without changing the target-head snapshot.
     * Modified files come from the request overlay, deleted paths are reported
     * as absent, and paths outside the PR change set use the exact target head.
     */
    public ReviewFileContent getReviewFileContent(
            String workspace,
            String repoSlug,
            String filePath
    ) throws IOException {
        if (!isRequestRepository(workspace, repoSlug)) {
            throw new IOException("Repository arguments do not match the request-bound repository");
        }
        if (reviewOverlay == null) {
            String detail = reviewOverlayFailure != null && !reviewOverlayFailure.isBlank()
                    ? ": " + reviewOverlayFailure
                    : "";
            throw new IOException("Request-scoped proposed tree is unavailable" + detail);
        }

        String normalizedPath = normalizeRepositoryPath(filePath);
        if (reviewOverlay.deletedFiles().contains(normalizedPath)) {
            return ReviewFileContent.deleted(normalizedPath);
        }
        if (reviewOverlay.changedFiles().contains(normalizedPath)) {
            try {
                String content = reviewOverlay.readFile(normalizedPath);
                return ReviewFileContent.changed(normalizedPath, content);
            } catch (NoSuchFileException unavailableContent) {
                return ReviewFileContent.unavailable(
                        normalizedPath,
                        "The file is modified in this pull request, but its proposed content was unavailable");
            }
        }

        String targetRef = targetRevision != null && !targetRevision.isBlank()
                ? targetRevision
                : targetBranch;
        return ReviewFileContent.targetHead(
                normalizedPath,
                getBranchFileContent(workspace, repoSlug, targetRef, normalizedPath));
    }

    private String targetFallbackRef() {
        return targetRevision != null && !targetRevision.isBlank()
                ? targetRevision
                : targetBranch;
    }

    private static String normalizeRepositoryPath(String rawPath) throws IOException {
        if (rawPath == null || rawPath.isBlank()) {
            throw new IOException("Repository file path is required");
        }
        try {
            Path normalized = Path.of(rawPath.replace('\\', '/')).normalize();
            String value = normalized.toString().replace('\\', '/');
            if (normalized.isAbsolute()
                    || value.isBlank()
                    || ".".equals(value)
                    || "..".equals(value)
                    || value.startsWith("../")) {
                throw new IOException("Repository path escapes the request-scoped tree: " + rawPath);
            }
            return value;
        } catch (InvalidPathException invalidPath) {
            throw new IOException("Invalid repository path: " + rawPath, invalidPath);
        }
    }

    public record ReviewFileContent(
            String filePath,
            String content,
            boolean exists,
            boolean changed,
            boolean deleted,
            boolean unavailable,
            String source,
            String reason
    ) {
        private static ReviewFileContent changed(String path, String content) {
            return new ReviewFileContent(
                    path, content, true, true, false, false, "review-overlay", null);
        }

        private static ReviewFileContent deleted(String path) {
            return new ReviewFileContent(
                    path, null, false, true, true, false, "review-overlay",
                    "The file is deleted in this pull request");
        }

        private static ReviewFileContent unavailable(String path, String reason) {
            return new ReviewFileContent(
                    path, null, false, true, false, true, "review-overlay", reason);
        }

        private static ReviewFileContent targetHead(String path, String content) {
            return new ReviewFileContent(
                    path, content, true, false, false, false, "target-head", null);
        }
    }

    private record ReviewOverlay(
            Path filesRoot,
            Path realFilesRoot,
            Set<String> changedFiles,
            Set<String> deletedFiles
    ) {
        private static ReviewOverlay open(String overlayPath) throws IOException {
            Path root = Path.of(overlayPath).toAbsolutePath().normalize();
            if (!Files.isDirectory(root, LinkOption.NOFOLLOW_LINKS)) {
                throw new IOException("Review overlay path is not a directory: " + root);
            }
            Path realRoot = root.toRealPath();
            Path manifestPath = root.resolve(REVIEW_OVERLAY_MANIFEST);
            Path realManifest = manifestPath.toRealPath();
            if (!realManifest.startsWith(realRoot) || !Files.isRegularFile(realManifest)) {
                throw new IOException("Review overlay manifest is invalid");
            }
            Path filesRoot = root.resolve(REVIEW_OVERLAY_FILES).normalize();
            Path realFilesRoot = filesRoot.toRealPath();
            if (!realFilesRoot.startsWith(realRoot) || !Files.isDirectory(realFilesRoot)) {
                throw new IOException("Review overlay files directory is invalid");
            }

            JsonNode manifest = OBJECT_MAPPER.readTree(realManifest.toFile());
            Set<String> changed = readManifestPaths(manifest.path("changedFiles"));
            Set<String> deleted = readManifestPaths(manifest.path("deletedFiles"));
            changed.addAll(deleted);
            return new ReviewOverlay(
                    filesRoot,
                    realFilesRoot,
                    Set.copyOf(changed),
                    Set.copyOf(deleted));
        }

        private static Set<String> readManifestPaths(JsonNode values) throws IOException {
            if (!values.isArray()) {
                throw new IOException("Review overlay manifest path list is invalid");
            }
            Set<String> paths = new LinkedHashSet<>();
            for (JsonNode value : values) {
                if (!value.isTextual()) {
                    throw new IOException("Review overlay manifest contains a non-text path");
                }
                paths.add(normalizeRepositoryPath(value.textValue()));
            }
            return paths;
        }

        private String readFile(String normalizedPath) throws IOException {
            Path candidate = filesRoot.resolve(normalizedPath).normalize();
            if (!candidate.startsWith(filesRoot)) {
                throw new IOException("Repository path escapes the request-scoped tree: " + normalizedPath);
            }
            if (!Files.exists(candidate, LinkOption.NOFOLLOW_LINKS)) {
                throw new NoSuchFileException(normalizedPath);
            }
            Path realCandidate = candidate.toRealPath();
            if (!realCandidate.startsWith(realFilesRoot)) {
                throw new IOException("Repository path escapes the request-scoped tree: " + normalizedPath);
            }
            if (!Files.isRegularFile(realCandidate)) {
                throw new IOException("Proposed-tree path is not a file: " + normalizedPath);
            }
            return Files.readString(realCandidate, StandardCharsets.UTF_8);
        }
    }

    @Override
    public String getRootDirectory(
            String workspace,
            String repoSlug,
            String branch
    ) throws IOException {
        if (!isLocalRef(workspace, repoSlug, branch)) {
            return providerClient().getRootDirectory(workspace, repoSlug, branch);
        }
        return readDirectory("");
    }

    @Override
    public String getDirectoryByPath(
            String workspace,
            String repoSlug,
            String branch,
            String dirPath
    ) throws IOException {
        if (!isLocalRef(workspace, repoSlug, branch)) {
            return providerClient().getDirectoryByPath(workspace, repoSlug, branch, dirPath);
        }
        try {
            return readDirectory(dirPath);
        } catch (NoSuchFileException absentFromSnapshot) {
            return providerClient().getDirectoryByPath(
                    workspace, repoSlug, targetFallbackRef(), dirPath);
        }
    }
}

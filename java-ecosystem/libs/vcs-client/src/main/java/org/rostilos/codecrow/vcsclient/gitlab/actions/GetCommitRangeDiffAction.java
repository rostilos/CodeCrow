package org.rostilos.codecrow.vcsclient.gitlab.actions;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.gitlab.GitLabConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

/**
 * Action to compare two commits in GitLab and get the diff.
 */
public class GetCommitRangeDiffAction {

    private static final Logger log = LoggerFactory.getLogger(GetCommitRangeDiffAction.class);
    private final OkHttpClient authorizedOkHttpClient;

    public GetCommitRangeDiffAction(OkHttpClient authorizedOkHttpClient) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
    }

    /**
     * Get the diff between two commits.
     * 
     * @param namespace the project namespace (group or user)
     * @param project the project path
     * @param baseCommitSha the base commit SHA
     * @param headCommitSha the head commit SHA
     * @return the diff as a unified diff string
     */
    public String getCommitRangeDiff(String namespace, String project, String baseCommitSha, String headCommitSha) throws IOException {
        String projectPath = namespace + "/" + project;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        
        // GitLab uses compare endpoint for commit range diff
        String apiUrl = String.format("%s/projects/%s/repository/compare?from=%s&to=%s",
                GitLabConfig.API_BASE, encodedPath, 
                URLEncoder.encode(baseCommitSha, StandardCharsets.UTF_8),
                URLEncoder.encode(headCommitSha, StandardCharsets.UTF_8));

        Request req = new Request.Builder()
                .url(apiUrl)
                .header("Accept", "application/json")
                .get()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                String body = resp.body() != null ? resp.body().string() : "";
                String msg = String.format("GitLab returned non-success response %d for URL %s: %s",
                        resp.code(), apiUrl, body);
                log.warn(msg);
                throw new IOException(msg);
            }
            
            String responseBody = resp.body() != null ? resp.body().string() : "{}";
            return buildUnifiedDiff(responseBody);
        }
    }

    /**
     * Build a unified diff from GitLab's compare response.
     */
    private String buildUnifiedDiff(String responseBody) throws IOException {
        StringBuilder combinedDiff = new StringBuilder();
        com.fasterxml.jackson.databind.ObjectMapper objectMapper = new com.fasterxml.jackson.databind.ObjectMapper();
        com.fasterxml.jackson.databind.JsonNode root = objectMapper.readTree(responseBody);
        if (root.path("compare_timeout").asBoolean(false)) {
            throw new IOException("GitLab compare response timed out and is incomplete");
        }
        com.fasterxml.jackson.databind.JsonNode diffs = root.get("diffs");

        if (diffs == null || !diffs.isArray()) {
            throw new IOException("GitLab compare response omitted its diffs array");
        }
        
        for (com.fasterxml.jackson.databind.JsonNode diffEntry : diffs) {
            if (diffEntry.path("collapsed").asBoolean(false)
                    || diffEntry.path("too_large").asBoolean(false)) {
                throw new IOException("GitLab omitted diff content for a collapsed or oversized file");
            }
            String oldPath = diffEntry.has("old_path") ? diffEntry.get("old_path").asText() : "";
            String newPath = diffEntry.has("new_path") ? diffEntry.get("new_path").asText() : "";
            String diff = diffEntry.has("diff") ? diffEntry.get("diff").asText() : "";
            boolean newFile = diffEntry.has("new_file") && diffEntry.get("new_file").asBoolean();
            boolean deletedFile = diffEntry.has("deleted_file") && diffEntry.get("deleted_file").asBoolean();
            boolean renamedFile = diffEntry.has("renamed_file") && diffEntry.get("renamed_file").asBoolean();
            String oldMode = diffEntry.path("a_mode").asText("");
            String newMode = diffEntry.path("b_mode").asText("");
            boolean modeOnly = !newFile && !deletedFile
                    && !oldMode.isBlank() && !newMode.isBlank()
                    && !oldMode.equals(newMode);
            boolean hasPatch = diff.lines().anyMatch(line -> line.startsWith("@@"))
                    || diff.contains("GIT binary patch")
                    || diff.contains("Binary files ");
            if (oldPath.isBlank() || newPath.isBlank()) {
                throw new IOException("GitLab compare response omitted a file path");
            }
            if (!hasPatch && !renamedFile && !modeOnly && !newFile && !deletedFile) {
                throw new IOException("GitLab compare response omitted patch content for " + newPath);
            }
            
            // Build unified diff header
            String fromFile = renamedFile ? oldPath : newPath;
            combinedDiff.append("diff --git ")
                    .append(renderGitPath("a/" + fromFile)).append(' ')
                    .append(renderGitPath("b/" + newPath)).append("\n");
            
            if (renamedFile) {
                combinedDiff.append("rename from ").append(renderGitPath(oldPath)).append("\n");
                combinedDiff.append("rename to ").append(renderGitPath(newPath)).append("\n");
            }
            if (modeOnly) {
                combinedDiff.append("old mode ").append(requireMode(oldMode)).append("\n");
                combinedDiff.append("new mode ").append(requireMode(newMode)).append("\n");
            }
            if (newFile) {
                combinedDiff.append("new file mode ").append(requireMode(newMode)).append("\n");
            } else if (deletedFile) {
                combinedDiff.append("deleted file mode ").append(requireMode(oldMode)).append("\n");
            }
            if (hasPatch) {
                if (newFile) {
                    combinedDiff.append("--- /dev/null\n");
                    combinedDiff.append("+++ ").append(renderGitPath("b/" + newPath)).append("\n");
                } else if (deletedFile) {
                    combinedDiff.append("--- ").append(renderGitPath("a/" + oldPath)).append("\n");
                    combinedDiff.append("+++ /dev/null\n");
                } else {
                    combinedDiff.append("--- ").append(renderGitPath("a/" + oldPath)).append("\n");
                    combinedDiff.append("+++ ").append(renderGitPath("b/" + newPath)).append("\n");
                }
            }
            
            // Append the actual diff content
            if (!diff.isEmpty()) {
                combinedDiff.append(diff);
                if (!diff.endsWith("\n")) {
                    combinedDiff.append("\n");
                }
            }

            combinedDiff.append("\n");
        }

        return combinedDiff.toString();
    }

    private static String requireMode(String mode) throws IOException {
        if (!mode.matches("[0-7]{6}")) {
            throw new IOException("GitLab compare response contained an invalid file mode");
        }
        return mode;
    }

    private static String renderGitPath(String path) {
        boolean quote = path.chars().anyMatch(character ->
                Character.isWhitespace(character) || character == '"'
                        || character == '\\' || Character.isISOControl(character));
        if (!quote) return path;

        StringBuilder rendered = new StringBuilder("\"");
        for (int index = 0; index < path.length(); index++) {
            char character = path.charAt(index);
            rendered.append(switch (character) {
                case '\\' -> "\\\\";
                case '"' -> "\\\"";
                case '\n' -> "\\n";
                case '\r' -> "\\r";
                case '\t' -> "\\t";
                default -> String.valueOf(character);
            });
        }
        return rendered.append('"').toString();
    }
}

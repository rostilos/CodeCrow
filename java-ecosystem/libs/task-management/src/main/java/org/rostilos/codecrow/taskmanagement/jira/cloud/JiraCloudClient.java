package org.rostilos.codecrow.taskmanagement.jira.cloud;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import okhttp3.*;
import org.rostilos.codecrow.core.util.RetryExecutor;
import org.rostilos.codecrow.taskmanagement.*;
import org.rostilos.codecrow.taskmanagement.model.TaskComment;
import org.rostilos.codecrow.taskmanagement.model.TaskDetails;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.time.OffsetDateTime;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.*;
import java.util.concurrent.TimeUnit;

/**
 * Jira Cloud REST API v3 implementation of {@link TaskManagementClient}.
 * <p>
 * Uses Basic Auth (email + API token) per
 * <a href="https://developer.atlassian.com/cloud/jira/platform/basic-auth-for-rest-apis/">
 *     Jira Cloud Basic Auth docs</a>.
 * </p>
 * <p>
 * All HTTP calls are wrapped with {@link RetryExecutor} for automatic
 * retry with exponential backoff on 429 / 5xx responses.
 * </p>
 */
public class JiraCloudClient implements TaskManagementClient {

    private static final Logger log = LoggerFactory.getLogger(JiraCloudClient.class);

    private static final MediaType JSON_MEDIA = MediaType.get("application/json; charset=utf-8");
    private static final String API_V3 = "/rest/api/3";

    private final JiraCloudConfig config;
    private final OkHttpClient httpClient;
    private final ObjectMapper objectMapper;

    public JiraCloudClient(JiraCloudConfig config) {
        this.config = config;
        this.objectMapper = new ObjectMapper();
        this.httpClient = new OkHttpClient.Builder()
                .connectTimeout(30, TimeUnit.SECONDS)
                .readTimeout(60, TimeUnit.SECONDS)
                .writeTimeout(60, TimeUnit.SECONDS)
                .addInterceptor(chain -> {
                    Request original = chain.request();
                    Request authorised = original.newBuilder()
                            .header("Authorization", config.basicAuthHeader())
                            .header("Accept", "application/json")
                            .header("Content-Type", "application/json")
                            .build();
                    return chain.proceed(authorised);
                })
                .build();
    }

    @Override
    public ETaskManagementPlatform getPlatform() {
        return ETaskManagementPlatform.JIRA_CLOUD;
    }


    @Override
    public boolean validateConnection() throws IOException {
        return RetryExecutor.withExponentialBackoff(() -> {
            Request request = new Request.Builder()
                    .url(config.baseUrl() + API_V3 + "/myself")
                    .get()
                    .build();

            try (Response response = httpClient.newCall(request).execute()) {
                if (response.code() == 401 || response.code() == 403) {
                    throw new TaskManagementException(
                            "Authentication failed — check email and API token",
                            response.code(), responseBodyString(response));
                }
                ensureSuccess(response, "validate connection");
                return true;
            }
        });
    }


    @Override
    public TaskDetails getTaskDetails(String taskId) throws IOException {
        return RetryExecutor.withExponentialBackoff(() -> {
            Request request = new Request.Builder()
                    .url(config.baseUrl() + API_V3 + "/issue/" + taskId
                         + "?fields=summary,description,status,assignee,reporter,priority,issuetype,created,updated")
                    .get()
                    .build();

            try (Response response = httpClient.newCall(request).execute()) {
                ensureSuccess(response, "get task details for " + taskId);
                JsonNode root = parseBody(response);
                JsonNode fields = root.path("fields");
                return new TaskDetails(
                        root.path("key").asText(),
                        fields.path("summary").asText(null),
                        extractDescription(fields.path("description")),
                        fields.path("status").path("name").asText(null),
                        fields.path("assignee").path("displayName").asText(null),
                        fields.path("reporter").path("displayName").asText(null),
                        fields.path("priority").path("name").asText(null),
                        fields.path("issuetype").path("name").asText(null),
                        parseDateTime(fields.path("created").asText(null)),
                        parseDateTime(fields.path("updated").asText(null)),
                        config.baseUrl() + "/browse/" + root.path("key").asText()
                );
            }
        });
    }


    @Override
    public List<TaskComment> getComments(String taskId) throws IOException {
        return RetryExecutor.withExponentialBackoff(() -> {
            Request request = new Request.Builder()
                    .url(config.baseUrl() + API_V3 + "/issue/" + taskId + "/comment?orderBy=created")
                    .get()
                    .build();

            try (Response response = httpClient.newCall(request).execute()) {
                ensureSuccess(response, "get comments for " + taskId);
                JsonNode root = parseBody(response);
                ArrayNode comments = (ArrayNode) root.path("comments");
                List<TaskComment> result = new ArrayList<>();
                for (JsonNode c : comments) {
                    result.add(parseComment(c));
                }
                return result;
            }
        });
    }

    @Override
    public TaskComment postComment(String taskId, String body) throws IOException {
        return RetryExecutor.withExponentialBackoff(() -> {
            ObjectNode payload = buildAdfCommentPayload(body);

            Request request = new Request.Builder()
                    .url(config.baseUrl() + API_V3 + "/issue/" + taskId + "/comment")
                    .post(RequestBody.create(objectMapper.writeValueAsBytes(payload), JSON_MEDIA))
                    .build();

            try (Response response = httpClient.newCall(request).execute()) {
                ensureSuccess(response, "post comment on " + taskId);
                return parseComment(parseBody(response));
            }
        });
    }

    @Override
    public TaskComment updateComment(String taskId, String commentId, String body) throws IOException {
        return RetryExecutor.withExponentialBackoff(() -> {
            ObjectNode payload = buildAdfCommentPayload(body);

            Request request = new Request.Builder()
                    .url(config.baseUrl() + API_V3 + "/issue/" + taskId + "/comment/" + commentId)
                    .put(RequestBody.create(objectMapper.writeValueAsBytes(payload), JSON_MEDIA))
                    .build();

            try (Response response = httpClient.newCall(request).execute()) {
                ensureSuccess(response, "update comment " + commentId + " on " + taskId);
                return parseComment(parseBody(response));
            }
        });
    }

    @Override
    public void deleteComment(String taskId, String commentId) throws IOException {
        RetryExecutor.<Void>withExponentialBackoff(() -> {
            Request request = new Request.Builder()
                    .url(config.baseUrl() + API_V3 + "/issue/" + taskId + "/comment/" + commentId)
                    .delete()
                    .build();

            try (Response response = httpClient.newCall(request).execute()) {
                ensureSuccess(response, "delete comment " + commentId + " on " + taskId);
                return null;
            }
        });
    }

    @Override
    public Optional<TaskComment> findCommentByMarker(String taskId, String marker) throws IOException {
        List<TaskComment> comments = getComments(taskId);
        return comments.stream()
                .filter(c -> c.body() != null && c.body().contains(marker))
                .findFirst();
    }

    /**
     * Build a Jira Cloud API v3 comment payload using Atlassian Document Format (ADF).
     * <p>
     * Converts common Markdown constructs to their ADF equivalents so that
     * headings, bold, italic, inline code, code blocks, lists, and horizontal
     * rules render correctly in Jira comments.
     * </p>
     */
    private ObjectNode buildAdfCommentPayload(String bodyText) {
        ObjectNode doc = objectMapper.createObjectNode();
        ObjectNode body = objectMapper.createObjectNode();
        body.put("version", 1);
        body.put("type", "doc");

        ArrayNode content = objectMapper.createArrayNode();

        String[] lines = bodyText.split("\n");
        int i = 0;
        while (i < lines.length) {
            String line = lines[i];

            // ── Fenced code block ───────────────────────────────
            if (line.stripLeading().startsWith("```")) {
                String lang = line.stripLeading().substring(3).strip();
                StringBuilder codeContent = new StringBuilder();
                i++;
                while (i < lines.length && !lines[i].stripLeading().startsWith("```")) {
                    if (codeContent.length() > 0) codeContent.append("\n");
                    codeContent.append(lines[i]);
                    i++;
                }
                i++; // skip closing ```
                content.add(buildCodeBlock(codeContent.toString(), lang.isEmpty() ? null : lang));
                continue;
            }

            // ── Blank line → skip ───────────────────────────────
            if (line.isBlank()) {
                i++;
                continue;
            }

            // ── Horizontal rule ─────────────────────────────────
            if (line.strip().matches("^-{3,}$|^\\*{3,}$|^_{3,}$")) {
                content.add(buildRule());
                i++;
                continue;
            }

            // ── Heading ─────────────────────────────────────────
            if (line.stripLeading().startsWith("#")) {
                int level = 0;
                String stripped = line.stripLeading();
                while (level < stripped.length() && stripped.charAt(level) == '#') level++;
                if (level >= 1 && level <= 6 && level < stripped.length() && stripped.charAt(level) == ' ') {
                    String headingText = stripped.substring(level + 1).strip();
                    content.add(buildHeading(headingText, level));
                    i++;
                    continue;
                }
            }

            // ── Unordered list ──────────────────────────────────
            if (line.stripLeading().matches("^[-*+]\\s+.*")) {
                ArrayNode items = objectMapper.createArrayNode();
                while (i < lines.length && lines[i].stripLeading().matches("^[-*+]\\s+.*")) {
                    String itemText = lines[i].stripLeading().replaceFirst("^[-*+]\\s+", "");
                    items.add(buildListItem(itemText));
                    i++;
                }
                content.add(buildBulletList(items));
                continue;
            }

            // ── Ordered list ────────────────────────────────────
            if (line.stripLeading().matches("^\\d+\\.\\s+.*")) {
                ArrayNode items = objectMapper.createArrayNode();
                while (i < lines.length && lines[i].stripLeading().matches("^\\d+\\.\\s+.*")) {
                    String itemText = lines[i].stripLeading().replaceFirst("^\\d+\\.\\s+", "");
                    items.add(buildListItem(itemText));
                    i++;
                }
                content.add(buildOrderedList(items));
                continue;
            }

            // ── Regular paragraph ───────────────────────────────
            // Collect consecutive non-blank, non-special lines
            StringBuilder paraText = new StringBuilder();
            while (i < lines.length && !lines[i].isBlank()
                    && !lines[i].stripLeading().startsWith("#")
                    && !lines[i].stripLeading().startsWith("```")
                    && !lines[i].stripLeading().matches("^[-*+]\\s+.*")
                    && !lines[i].stripLeading().matches("^\\d+\\.\\s+.*")
                    && !lines[i].strip().matches("^-{3,}$|^\\*{3,}$|^_{3,}$")) {
                if (paraText.length() > 0) paraText.append(" ");
                paraText.append(lines[i].strip());
                i++;
            }
            if (paraText.length() > 0) {
                content.add(buildParagraph(paraText.toString()));
            }
        }

        body.set("content", content);
        doc.set("body", body);
        return doc;
    }

    // ─── ADF node builders ───────────────────────────────────────────

    private ObjectNode buildHeading(String text, int level) {
        ObjectNode heading = objectMapper.createObjectNode();
        heading.put("type", "heading");
        ObjectNode attrs = objectMapper.createObjectNode();
        attrs.put("level", level);
        heading.set("attrs", attrs);
        heading.set("content", buildInlineContent(text));
        return heading;
    }

    private ObjectNode buildParagraph(String text) {
        ObjectNode paragraph = objectMapper.createObjectNode();
        paragraph.put("type", "paragraph");
        paragraph.set("content", buildInlineContent(text));
        return paragraph;
    }

    private ObjectNode buildCodeBlock(String code, String language) {
        ObjectNode block = objectMapper.createObjectNode();
        block.put("type", "codeBlock");
        if (language != null && !language.isEmpty()) {
            ObjectNode attrs = objectMapper.createObjectNode();
            attrs.put("language", language);
            block.set("attrs", attrs);
        }
        ArrayNode codeContent = objectMapper.createArrayNode();
        ObjectNode textNode = objectMapper.createObjectNode();
        textNode.put("type", "text");
        textNode.put("text", code);
        codeContent.add(textNode);
        block.set("content", codeContent);
        return block;
    }

    private ObjectNode buildRule() {
        ObjectNode rule = objectMapper.createObjectNode();
        rule.put("type", "rule");
        return rule;
    }

    private ObjectNode buildBulletList(ArrayNode items) {
        ObjectNode list = objectMapper.createObjectNode();
        list.put("type", "bulletList");
        list.set("content", items);
        return list;
    }

    private ObjectNode buildOrderedList(ArrayNode items) {
        ObjectNode list = objectMapper.createObjectNode();
        list.put("type", "orderedList");
        list.set("content", items);
        return list;
    }

    private ObjectNode buildListItem(String text) {
        ObjectNode li = objectMapper.createObjectNode();
        li.put("type", "listItem");
        ArrayNode liContent = objectMapper.createArrayNode();
        ObjectNode para = objectMapper.createObjectNode();
        para.put("type", "paragraph");
        para.set("content", buildInlineContent(text));
        liContent.add(para);
        li.set("content", liContent);
        return li;
    }

    /**
     * Parse inline Markdown formatting into ADF inline nodes.
     * <p>
     * Handles: {@code **bold**}, {@code __bold__}, {@code ***bold-italic***},
     * {@code *italic*}/{@code _italic_}, {@code `code`}, and backslash escapes
     * (e.g. {@code \*not italic\*}).
     * </p>
     */
    private ArrayNode buildInlineContent(String text) {
        ArrayNode nodes = objectMapper.createArrayNode();
        int pos = 0;
        int len = text.length();
        StringBuilder plain = new StringBuilder();

        while (pos < len) {
            char c = text.charAt(pos);

            // ── Backslash escape: \* \_ \` \\ ──
            if (c == '\\' && pos + 1 < len) {
                char next = text.charAt(pos + 1);
                if (next == '*' || next == '_' || next == '`' || next == '\\') {
                    plain.append(next);
                    pos += 2;
                    continue;
                }
            }

            // ── Inline code: `...` (no nesting, no escapes inside) ──
            if (c == '`') {
                int end = text.indexOf('`', pos + 1);
                if (end > pos) {
                    flushPlain(nodes, plain);
                    addTextNode(nodes, text.substring(pos + 1, end), List.of("code"));
                    pos = end + 1;
                    continue;
                }
            }

            // ── Bold-italic: ***...*** ──
            if (pos + 2 < len && c == '*' && text.charAt(pos + 1) == '*' && text.charAt(pos + 2) == '*') {
                int end = text.indexOf("***", pos + 3);
                if (end > pos) {
                    flushPlain(nodes, plain);
                    addTextNode(nodes, text.substring(pos + 3, end), List.of("strong", "em"));
                    pos = end + 3;
                    continue;
                }
            }

            // ── Bold: **...** or __...__ ──
            if (pos + 1 < len && ((c == '*' && text.charAt(pos + 1) == '*')
                                || (c == '_' && text.charAt(pos + 1) == '_'))) {
                String marker = text.substring(pos, pos + 2);
                int end = text.indexOf(marker, pos + 2);
                if (end > pos) {
                    flushPlain(nodes, plain);
                    addTextNode(nodes, text.substring(pos + 2, end), List.of("strong"));
                    pos = end + 2;
                    continue;
                }
            }

            // ── Italic: *...* or _..._ (single marker, not doubled) ──
            if (c == '*' || c == '_') {
                if (!(pos + 1 < len && text.charAt(pos + 1) == c)) {
                    int end = text.indexOf(c, pos + 1);
                    if (end > pos) {
                        flushPlain(nodes, plain);
                        addTextNode(nodes, text.substring(pos + 1, end), List.of("em"));
                        pos = end + 1;
                        continue;
                    }
                }
            }

            // ── Regular character ──
            plain.append(c);
            pos++;
        }

        flushPlain(nodes, plain);
        return nodes;
    }

    /** Flush accumulated plain text into a text node, then clear the buffer. */
    private void flushPlain(ArrayNode nodes, StringBuilder buffer) {
        if (buffer.isEmpty()) {
            return;
        }
        addTextNode(nodes, buffer.toString(), List.of());
        buffer.setLength(0);
    }

    private void addTextNode(ArrayNode nodes, String text, List<String> markTypes) {
        ObjectNode textNode = objectMapper.createObjectNode();
        textNode.put("type", "text");
        textNode.put("text", text);
        if (!markTypes.isEmpty()) {
            ArrayNode marks = objectMapper.createArrayNode();
            for (String markType : markTypes) {
                ObjectNode mark = objectMapper.createObjectNode();
                mark.put("type", markType);
                marks.add(mark);
            }
            textNode.set("marks", marks);
        }
        nodes.add(textNode);
    }

    // ─── Response helpers ────────────────────────────────────────────

    private void ensureSuccess(Response response, String operation) throws IOException {
        if (response.isSuccessful()) {
            return;
        }
        String body = responseBodyString(response);
        int code = response.code();

        if (code == 404) {
            throw new TaskManagementException("Not found: " + operation, code, body);
        }
        if (code == 401 || code == 403) {
            throw new TaskManagementException("Authentication/authorization failed: " + operation, code, body);
        }
        if (code == 429) {
            // Let RetryExecutor handle rate limits — rethrow as IOException
            throw new IOException("Rate limited (429) during: " + operation);
        }
        if (code >= 500) {
            throw new IOException("Server error (" + code + ") during: " + operation + " — " + body);
        }
        throw new TaskManagementException("Failed to " + operation + " (HTTP " + code + ")", code, body);
    }

    private JsonNode parseBody(Response response) throws IOException {
        ResponseBody body = response.body();
        if (body == null) {
            throw new IOException("Empty response body from Jira");
        }
        return objectMapper.readTree(body.byteStream());
    }

    private String responseBodyString(Response response) {
        try {
            ResponseBody body = response.body();
            return body != null ? body.string() : "";
        } catch (IOException e) {
            return "<unable to read response body>";
        }
    }

    private TaskComment parseComment(JsonNode node) {
        return new TaskComment(
                node.path("id").asText(),
                node.path("author").path("displayName").asText(null),
                extractPlainTextFromAdf(node.path("body")),
                parseDateTime(node.path("created").asText(null)),
                parseDateTime(node.path("updated").asText(null))
        );
    }

    /**
     * Extract plain text from a Jira ADF (Atlassian Document Format) node.
     * Traverses the content tree and concatenates all text nodes.
     */
    private String extractPlainTextFromAdf(JsonNode adfNode) {
        if (adfNode == null || adfNode.isMissingNode()) {
            return null;
        }
        StringBuilder sb = new StringBuilder();
        extractTextRecursive(adfNode, sb);
        return sb.toString().strip();
    }

    private void extractTextRecursive(JsonNode node, StringBuilder sb) {
        if (node.has("text")) {
            sb.append(node.path("text").asText());
        }
        JsonNode content = node.path("content");
        if (content.isArray()) {
            for (JsonNode child : content) {
                extractTextRecursive(child, sb);
            }
            // Add paragraph separation
            if ("paragraph".equals(node.path("type").asText())) {
                sb.append("\n\n");
            }
        }
    }

    /**
     * Extract description as plain text from Jira v3 ADF format.
     */
    private String extractDescription(JsonNode descriptionNode) {
        if (descriptionNode == null || descriptionNode.isMissingNode() || descriptionNode.isNull()) {
            return null;
        }
        // v3 API returns ADF; extract plain text
        return extractPlainTextFromAdf(descriptionNode);
    }

    private OffsetDateTime parseDateTime(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        try {
            return OffsetDateTime.parse(value, DateTimeFormatter.ISO_OFFSET_DATE_TIME);
        } catch (DateTimeParseException e) {
            log.debug("Failed to parse datetime '{}': {}", value, e.getMessage());
            return null;
        }
    }
}

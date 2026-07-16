package org.rostilos.codecrow.vcsclient.diff;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Objects;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import static org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory.ChangeStatus.ADD;
import static org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory.ChangeStatus.COPY;
import static org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory.ChangeStatus.DELETE;
import static org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory.ChangeStatus.MODIFY;
import static org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory.ChangeStatus.RENAME;
import static org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory.Completeness.COMPLETE;
import static org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory.Completeness.INCOMPLETE;
import static org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory.GapType.MALFORMED;

/**
 * Bounded state-machine parser for git-style unified diffs.
 */
public final class ExactDiffInventoryParser {

    private static final String DIFF_HEADER = "diff --git ";
    private static final Pattern HUNK_HEADER = Pattern.compile(
            "^@@ -(\\d+)(?:,(\\d+))? \\+(\\d+)(?:,(\\d+))? @@(?:.*)?$"
    );
    private static final Pattern INDEX_MODE = Pattern.compile(
            "^index \\S+\\.\\.\\S+ ([0-7]{6})$"
    );
    private static final Comparator<ExactDiffInventory.Entry> CANONICAL_ENTRY_ORDER =
            Comparator.comparing(ExactDiffInventoryParser::canonicalPath)
                    .thenComparing(entry -> nullToEmpty(entry.oldPath()))
                    .thenComparing(entry -> nullToEmpty(entry.newPath()))
                    .thenComparing(entry -> entry.status().name());

    public ExactDiffInventory parse(String rawDiff) {
        return parse(rawDiff, List.of());
    }

    public ExactDiffInventory parse(
            String rawDiff,
            List<ExactDiffInventory.Gap> declaredGaps
    ) {
        Objects.requireNonNull(rawDiff, "rawDiff");
        Objects.requireNonNull(declaredGaps, "declaredGaps");

        byte[] rawBytes = rawDiff.getBytes(StandardCharsets.UTF_8);
        ExactDiffInventory.RawDiffProvenance provenance =
                new ExactDiffInventory.RawDiffProvenance(
                        "SHA-256",
                        sha256(rawBytes),
                        rawBytes.length
                );
        List<ExactDiffInventory.Gap> gaps = new ArrayList<>(declaredGaps);

        if (rawDiff.isEmpty()) {
            return inventory(provenance, List.of(), gaps);
        }

        List<Line> lines = scanLines(rawDiff);
        List<Integer> sectionLineIndexes = new ArrayList<>();
        for (int lineIndex = 0; lineIndex < lines.size(); lineIndex++) {
            if (lines.get(lineIndex).content().startsWith(DIFF_HEADER)) {
                sectionLineIndexes.add(lineIndex);
            }
        }

        if (sectionLineIndexes.isEmpty()) {
            gaps.add(new ExactDiffInventory.Gap(
                    MALFORMED,
                    "Nonblank diff contains no valid file section"
            ));
            return inventory(provenance, List.of(), gaps);
        }

        int firstSectionOffset = lines.get(sectionLineIndexes.get(0)).startOffset();
        if (!rawDiff.substring(0, firstSectionOffset).isBlank()) {
            gaps.add(new ExactDiffInventory.Gap(
                    MALFORMED,
                    "Nonblank content precedes the first file section"
            ));
        }

        List<ExactDiffInventory.Entry> entries = new ArrayList<>();
        for (int sectionIndex = 0; sectionIndex < sectionLineIndexes.size(); sectionIndex++) {
            int firstLineIndex = sectionLineIndexes.get(sectionIndex);
            int endLineIndex = sectionIndex + 1 < sectionLineIndexes.size()
                    ? sectionLineIndexes.get(sectionIndex + 1)
                    : lines.size();
            int startOffset = lines.get(firstLineIndex).startOffset();
            int endOffset = sectionIndex + 1 < sectionLineIndexes.size()
                    ? lines.get(endLineIndex).startOffset()
                    : rawDiff.length();
            String rawSection = rawDiff.substring(startOffset, endOffset);

            SectionResult result = parseSection(
                    lines.subList(firstLineIndex, endLineIndex),
                    rawSection
            );
            if (result.entry() != null) {
                entries.add(result.entry());
            }
            if (result.malformedDetail() != null) {
                gaps.add(new ExactDiffInventory.Gap(MALFORMED, result.malformedDetail()));
            }
        }

        entries.sort(CANONICAL_ENTRY_ORDER);
        return inventory(provenance, entries, gaps);
    }

    private static ExactDiffInventory inventory(
            ExactDiffInventory.RawDiffProvenance provenance,
            List<ExactDiffInventory.Entry> entries,
            List<ExactDiffInventory.Gap> gaps
    ) {
        return new ExactDiffInventory(
                provenance,
                entries,
                gaps.isEmpty() ? COMPLETE : INCOMPLETE,
                gaps
        );
    }

    private static SectionResult parseSection(List<Line> lines, String rawSection) {
        HeaderPaths headerPaths;
        try {
            headerPaths = parseHeader(lines.get(0).content());
        } catch (PathParseException exception) {
            return SectionResult.malformed(
                    "Malformed file header: " + exception.getMessage()
            );
        }

        SectionState state = new SectionState(headerPaths.oldPath(), headerPaths.newPath());
        for (int lineIndex = 1; lineIndex < lines.size(); lineIndex++) {
            state.accept(lines.get(lineIndex).content());
        }

        try {
            ExactDiffInventory.Entry entry = state.toEntry(sha256(rawSection));
            return new SectionResult(entry, state.malformedDetail());
        } catch (IllegalArgumentException exception) {
            return SectionResult.malformed("Malformed file section: " + exception.getMessage());
        }
    }

    private static HeaderPaths parseHeader(String header) throws PathParseException {
        if (!header.startsWith(DIFF_HEADER)) {
            throw new PathParseException("missing diff --git marker");
        }
        String renderedPaths = header.substring(DIFF_HEADER.length());
        List<String> paths = parseHeaderPaths(renderedPaths);
        if (paths.size() != 2) {
            throw new PathParseException("expected exactly two paths");
        }
        String oldPath = normalizePath(paths.get(0), "a/");
        String newPath = normalizePath(paths.get(1), "b/");
        if (oldPath == null && newPath == null) {
            throw new PathParseException("both paths resolve to /dev/null");
        }
        return new HeaderPaths(oldPath, newPath);
    }

    private static List<String> parseHeaderPaths(String renderedPaths)
            throws PathParseException {
        if (!renderedPaths.startsWith("\"") && renderedPaths.startsWith("a/")) {
            int newPathBoundary = renderedPaths.indexOf(" b/", 2);
            if (newPathBoundary >= 0) {
                return List.of(
                        renderedPaths.substring(0, newPathBoundary),
                        renderedPaths.substring(newPathBoundary + 1)
                );
            }
        }
        return parseTokens(renderedPaths);
    }

    private static List<String> parseTokens(String value) throws PathParseException {
        List<String> tokens = new ArrayList<>(2);
        int offset = 0;
        while (offset < value.length()) {
            while (offset < value.length() && Character.isWhitespace(value.charAt(offset))) {
                offset++;
            }
            if (offset == value.length()) {
                break;
            }
            if (value.charAt(offset) == '"') {
                ParsedToken token = parseQuotedToken(value, offset);
                tokens.add(token.value());
                offset = token.endOffset();
            } else {
                int end = offset;
                while (end < value.length() && !Character.isWhitespace(value.charAt(end))) {
                    end++;
                }
                tokens.add(value.substring(offset, end));
                offset = end;
            }
        }
        return tokens;
    }

    private static ParsedToken parseQuotedToken(String value, int openingQuote)
            throws PathParseException {
        ByteArrayOutputStream decoded = new ByteArrayOutputStream();
        int offset = openingQuote + 1;
        while (offset < value.length()) {
            char current = value.charAt(offset);
            if (current == '"') {
                return new ParsedToken(decodeUtf8(decoded), offset + 1);
            }
            if (current != '\\') {
                int codePoint = value.codePointAt(offset);
                byte[] encoded = new String(Character.toChars(codePoint))
                        .getBytes(StandardCharsets.UTF_8);
                decoded.writeBytes(encoded);
                offset += Character.charCount(codePoint);
                continue;
            }

            offset++;
            if (offset >= value.length()) {
                throw new PathParseException("unterminated escape in quoted path");
            }
            char escaped = value.charAt(offset);
            if (escaped >= '0' && escaped <= '7') {
                int octal = 0;
                int digits = 0;
                while (offset < value.length()
                        && digits < 3
                        && value.charAt(offset) >= '0'
                        && value.charAt(offset) <= '7') {
                    octal = octal * 8 + value.charAt(offset) - '0';
                    offset++;
                    digits++;
                }
                decoded.write(octal & 0xff);
                continue;
            }

            decoded.write(switch (escaped) {
                case 'a' -> 0x07;
                case 'b' -> '\b';
                case 't' -> '\t';
                case 'n' -> '\n';
                case 'v' -> 0x0b;
                case 'f' -> '\f';
                case 'r' -> '\r';
                case '"' -> '"';
                case '\\' -> '\\';
                default -> throw new PathParseException(
                        "unsupported escape in quoted path: \\" + escaped
                );
            });
            offset++;
        }
        throw new PathParseException("unterminated quoted path");
    }

    private static String decodeUtf8(ByteArrayOutputStream encoded) throws PathParseException {
        try {
            return StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(encoded.toByteArray()))
                    .toString();
        } catch (CharacterCodingException exception) {
            throw new PathParseException("quoted path is not valid UTF-8");
        }
    }

    private static String parseMetadataPath(String value) throws PathParseException {
        String candidate = value.strip();
        if (candidate.startsWith("\"")) {
            ParsedToken token = parseQuotedToken(candidate, 0);
            if (!candidate.substring(token.endOffset()).isBlank()) {
                throw new PathParseException("unexpected text after quoted path");
            }
            return token.value();
        }
        return candidate;
    }

    private static String parseMarkerPath(String value) throws PathParseException {
        String candidate = value.stripLeading();
        if (candidate.startsWith("\"")) {
            return parseQuotedToken(candidate, 0).value();
        }
        int timestampSeparator = candidate.indexOf('\t');
        return timestampSeparator >= 0
                ? candidate.substring(0, timestampSeparator)
                : candidate;
    }

    private static String normalizePath(String path, String sidePrefix) {
        if ("/dev/null".equals(path)) {
            return null;
        }
        return path.startsWith(sidePrefix) ? path.substring(sidePrefix.length()) : path;
    }

    private static List<Line> scanLines(String value) {
        List<Line> lines = new ArrayList<>();
        int lineStart = 0;
        for (int offset = 0; offset < value.length(); offset++) {
            if (value.charAt(offset) != '\n') {
                continue;
            }
            int contentEnd = offset > lineStart && value.charAt(offset - 1) == '\r'
                    ? offset - 1
                    : offset;
            lines.add(new Line(value.substring(lineStart, contentEnd), lineStart));
            lineStart = offset + 1;
        }
        if (lineStart < value.length()) {
            lines.add(new Line(value.substring(lineStart), lineStart));
        }
        return lines;
    }

    private static String sha256(String value) {
        return sha256(value.getBytes(StandardCharsets.UTF_8));
    }

    private static String sha256(byte[] value) {
        try {
            return java.util.HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(value)
            );
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("The JDK must provide SHA-256", exception);
        }
    }

    private static String canonicalPath(ExactDiffInventory.Entry entry) {
        return entry.newPath() != null ? entry.newPath() : entry.oldPath();
    }

    private static String nullToEmpty(String value) {
        return value == null ? "" : value;
    }

    private record Line(String content, int startOffset) {
    }

    private record HeaderPaths(String oldPath, String newPath) {
    }

    private record ParsedToken(String value, int endOffset) {
    }

    private record SectionResult(
            ExactDiffInventory.Entry entry,
            String malformedDetail
    ) {

        private static SectionResult malformed(String detail) {
            return new SectionResult(null, detail);
        }
    }

    private static final class SectionState {

        private final String headerOldPath;
        private final String headerNewPath;
        private String oldPath;
        private String newPath;
        private ExactDiffInventory.ChangeStatus status = MODIFY;
        private final List<ExactDiffInventory.Hunk> hunks = new ArrayList<>();
        private boolean binary;
        private String oldMode;
        private String newMode;
        private String malformedDetail;
        private boolean recognizedChange;
        private int remainingOldHunkLines = -1;
        private int remainingNewHunkLines = -1;

        private SectionState(String oldPath, String newPath) {
            this.headerOldPath = oldPath;
            this.headerNewPath = newPath;
            this.oldPath = oldPath;
            this.newPath = newPath;
        }

        private void accept(String line) {
            if (remainingOldHunkLines >= 0) {
                if (line.startsWith("@@")) {
                    finishActiveHunk();
                    acceptHunk(line);
                } else {
                    acceptHunkLine(line);
                }
                return;
            }

            try {
                if (line.startsWith("new file mode ")) {
                    status = ADD;
                    oldPath = null;
                    newMode = line.substring("new file mode ".length()).strip();
                    recognizedChange = true;
                } else if (line.startsWith("deleted file mode ")) {
                    status = DELETE;
                    newPath = null;
                    oldMode = line.substring("deleted file mode ".length()).strip();
                    recognizedChange = true;
                } else if (line.startsWith("old mode ")) {
                    oldMode = line.substring("old mode ".length()).strip();
                    recognizedChange = true;
                } else if (line.startsWith("new mode ")) {
                    newMode = line.substring("new mode ".length()).strip();
                    recognizedChange = true;
                } else if (line.startsWith("rename from ")) {
                    status = RENAME;
                    oldPath = reconcilePath(
                            oldPath,
                            normalizePath(
                                    parseMetadataPath(line.substring("rename from ".length())),
                                    "a/"),
                            headerOldPath,
                            "rename from");
                    recognizedChange = true;
                } else if (line.startsWith("rename to ")) {
                    status = RENAME;
                    newPath = reconcilePath(
                            newPath,
                            normalizePath(
                                    parseMetadataPath(line.substring("rename to ".length())),
                                    "b/"),
                            headerNewPath,
                            "rename to");
                    recognizedChange = true;
                } else if (line.startsWith("copy from ")) {
                    status = COPY;
                    oldPath = reconcilePath(
                            oldPath,
                            normalizePath(
                                    parseMetadataPath(line.substring("copy from ".length())),
                                    "a/"),
                            headerOldPath,
                            "copy from");
                    recognizedChange = true;
                } else if (line.startsWith("copy to ")) {
                    status = COPY;
                    newPath = reconcilePath(
                            newPath,
                            normalizePath(
                                    parseMetadataPath(line.substring("copy to ".length())),
                                    "b/"),
                            headerNewPath,
                            "copy to");
                    recognizedChange = true;
                } else if (line.startsWith("--- ")) {
                    String markerPath = normalizePath(
                            parseMarkerPath(line.substring("--- ".length())),
                            "a/"
                    );
                    if (markerPath == null) {
                        status = ADD;
                        oldPath = null;
                        recognizedChange = true;
                    } else {
                        oldPath = reconcilePath(
                                oldPath, markerPath, headerOldPath, "--- marker");
                    }
                } else if (line.startsWith("+++ ")) {
                    String markerPath = normalizePath(
                            parseMarkerPath(line.substring("+++ ".length())),
                            "b/"
                    );
                    if (markerPath == null) {
                        status = DELETE;
                        newPath = null;
                        recognizedChange = true;
                    } else {
                        newPath = reconcilePath(
                                newPath, markerPath, headerNewPath, "+++ marker");
                    }
                } else if (line.startsWith("Binary files ") || line.equals("GIT binary patch")) {
                    binary = true;
                    recognizedChange = true;
                } else if (line.startsWith("@@")) {
                    acceptHunk(line);
                } else {
                    acceptIndexMode(line);
                }
            } catch (PathParseException exception) {
                markMalformed("Malformed path metadata: " + exception.getMessage());
            }
        }

        private String reconcilePath(
                String currentPath,
                String metadataPath,
                String headerPath,
                String source
        ) {
            if (metadataPath == null || !metadataPath.equals(headerPath)) {
                markMalformed(source + " path conflicts with diff --git header");
                return currentPath;
            }
            return metadataPath;
        }

        private void acceptIndexMode(String line) {
            Matcher matcher = INDEX_MODE.matcher(line);
            if (matcher.matches()) {
                String mode = matcher.group(1);
                if (oldMode == null && status != ADD) {
                    oldMode = mode;
                }
                if (newMode == null && status != DELETE) {
                    newMode = mode;
                }
            }
        }

        private void acceptHunk(String line) {
            Matcher matcher = HUNK_HEADER.matcher(line);
            if (!matcher.matches()) {
                markMalformed("Malformed hunk header: " + line);
                return;
            }
            try {
                int oldLineCount = countOrOne(matcher.group(2));
                int newLineCount = countOrOne(matcher.group(4));
                hunks.add(new ExactDiffInventory.Hunk(
                        new ExactDiffInventory.LineRange(
                                Integer.parseInt(matcher.group(1)),
                                oldLineCount
                        ),
                        new ExactDiffInventory.LineRange(
                                Integer.parseInt(matcher.group(3)),
                                newLineCount
                        )
                ));
                recognizedChange = true;
                remainingOldHunkLines = oldLineCount;
                remainingNewHunkLines = newLineCount;
                closeConsumedHunk();
            } catch (IllegalArgumentException exception) {
                markMalformed("Invalid hunk range: " + line);
            }
        }

        private void acceptHunkLine(String line) {
            if (line.startsWith("\\ No newline at end of file")) {
                return;
            }
            if (line.isEmpty()) {
                markMalformed("Malformed empty line inside hunk");
                clearActiveHunk();
                return;
            }

            switch (line.charAt(0)) {
                case ' ' -> {
                    remainingOldHunkLines--;
                    remainingNewHunkLines--;
                }
                case '-' -> remainingOldHunkLines--;
                case '+' -> remainingNewHunkLines--;
                default -> {
                    markMalformed("Malformed hunk body line");
                    clearActiveHunk();
                    return;
                }
            }

            if (remainingOldHunkLines < 0 || remainingNewHunkLines < 0) {
                markMalformed("Hunk body exceeds its declared range");
                clearActiveHunk();
                return;
            }
            closeConsumedHunk();
        }

        private void closeConsumedHunk() {
            if (remainingOldHunkLines == 0 && remainingNewHunkLines == 0) {
                clearActiveHunk();
            }
        }

        private void finishActiveHunk() {
            if (remainingOldHunkLines > 0 || remainingNewHunkLines > 0) {
                markMalformed("Hunk body does not satisfy its declared range");
            }
            clearActiveHunk();
        }

        private void clearActiveHunk() {
            remainingOldHunkLines = -1;
            remainingNewHunkLines = -1;
        }

        private ExactDiffInventory.Entry toEntry(String rawPatchSha256) {
            finishActiveHunk();
            if (!recognizedChange) {
                markMalformed("File section contains no recognized change metadata");
            }
            return new ExactDiffInventory.Entry(
                    oldPath,
                    newPath,
                    status,
                    hunks,
                    binary,
                    oldMode,
                    newMode,
                    rawPatchSha256
            );
        }

        private String malformedDetail() {
            return malformedDetail;
        }

        private void markMalformed(String detail) {
            if (malformedDetail == null) {
                malformedDetail = detail;
            }
        }

        private static int countOrOne(String count) {
            return count == null ? 1 : Integer.parseInt(count);
        }
    }

    private static final class PathParseException extends Exception {

        private PathParseException(String message) {
            super(message);
        }
    }
}

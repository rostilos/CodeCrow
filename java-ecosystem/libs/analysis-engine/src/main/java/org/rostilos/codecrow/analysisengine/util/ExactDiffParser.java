package org.rostilos.codecrow.analysisengine.util;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

import org.rostilos.codecrow.analysisengine.util.DiffParsingUtils.ChangeType;
import org.rostilos.codecrow.analysisengine.util.DiffParsingUtils.FileChange;

/** Strict parser for immutable provider diffs, including Git C-quoted paths. */
public final class ExactDiffParser {
    private static final String HEADER = "diff --git ";

    private ExactDiffParser() {}

    public static List<FileChange> parse(String rawDiff) {
        if (rawDiff == null || rawDiff.isBlank()) return List.of();

        List<FileChange> changes = new ArrayList<>();
        StringBuilder section = null;
        String header = null;
        String[] lines = rawDiff.split("\\r?\\n", -1);
        for (int lineIndex = 0; lineIndex < lines.length; lineIndex++) {
            String line = lines[lineIndex];
            if (line.startsWith(HEADER)) {
                if (section != null) changes.add(parseSection(header, section.toString()));
                header = line;
                section = new StringBuilder();
            } else if (section == null) {
                if (!line.isBlank()) {
                    throw new IllegalArgumentException(
                            "Exact diff contains content before its first file header");
                }
                continue;
            }
            section.append(line);
            if (lineIndex < lines.length - 1) section.append('\n');
        }
        if (section == null) {
            throw new IllegalArgumentException("Exact diff contains no file sections");
        }
        changes.add(parseSection(header, section.toString()));
        return List.copyOf(changes);
    }

    private static FileChange parseSection(String header, String section) {
        PathPair headerPaths = parseHeader(header);
        String oldPath = null;
        String newPath = null;
        String markerOldPath = null;
        String markerNewPath = null;
        String renameFrom = null;
        String renameTo = null;
        boolean oldMarkerSeen = false;
        boolean newMarkerSeen = false;
        boolean added = false;
        boolean deleted = false;

        for (String line : section.split("\\r?\\n")) {
            if (line.startsWith("--- ")) {
                markerOldPath = parseMarkerPath(line.substring(4), "a/");
                oldPath = markerOldPath;
                oldMarkerSeen = true;
                added = oldPath == null;
            } else if (line.startsWith("+++ ")) {
                markerNewPath = parseMarkerPath(line.substring(4), "b/");
                newPath = markerNewPath;
                newMarkerSeen = true;
                deleted = newPath == null;
            } else if (line.startsWith("new file mode ")) {
                added = true;
            } else if (line.startsWith("deleted file mode ")) {
                deleted = true;
            } else if (line.startsWith("rename from ")) {
                renameFrom = decodePath(line.substring("rename from ".length()));
            } else if (line.startsWith("rename to ")) {
                renameTo = decodePath(line.substring("rename to ".length()));
            }
        }

        if (renameFrom != null || renameTo != null) {
            if (renameFrom == null || renameTo == null) {
                throw new IllegalArgumentException("Exact diff contains an incomplete rename");
            }
            oldPath = renameFrom;
            newPath = renameTo;
        } else {
            if (!added && oldPath == null) oldPath = headerPaths.oldPath();
            if (!deleted && newPath == null) newPath = headerPaths.newPath();
        }
        if ((renameFrom != null && !renameFrom.equals(headerPaths.oldPath()))
                || (renameTo != null && !renameTo.equals(headerPaths.newPath()))
                || (oldMarkerSeen && markerOldPath != null
                        && !markerOldPath.equals(headerPaths.oldPath()))
                || (newMarkerSeen && markerNewPath != null
                        && !markerNewPath.equals(headerPaths.newPath()))) {
            throw new IllegalArgumentException(
                    "Exact diff path markers do not agree with the file header");
        }
        if (oldPath == null && newPath == null) {
            throw new IllegalArgumentException("Exact diff file section has no repository path");
        }

        ChangeType type = renameFrom != null
                ? ChangeType.RENAMED
                : added ? ChangeType.ADDED : deleted ? ChangeType.DELETED : ChangeType.MODIFIED;
        if (type == ChangeType.ADDED) oldPath = null;
        if (type == ChangeType.DELETED) newPath = null;
        return new FileChange(oldPath, newPath, type, section);
    }

    private static PathPair parseHeader(String header) {
        if (header == null || !header.startsWith(HEADER)) {
            throw new IllegalArgumentException("Malformed exact diff file header");
        }
        String body = header.substring(HEADER.length());
        String oldToken;
        String newToken;
        if (body.startsWith("\"")) {
            Token old = quotedToken(body, 0);
            int next = skipSpaces(body, old.end());
            Token destination = body.startsWith("\"", next)
                    ? quotedToken(body, next)
                    : new Token(body.substring(next), body.length());
            oldToken = old.value();
            newToken = destination.value();
        } else {
            int quotedSeparator = body.lastIndexOf(" \"b/");
            int separator = quotedSeparator >= 0
                    ? quotedSeparator
                    : body.lastIndexOf(" b/");
            if (!body.startsWith("a/") || separator < 0) {
                throw new IllegalArgumentException("Malformed exact diff path header: " + header);
            }
            oldToken = body.substring(0, separator);
            String destination = body.substring(separator + 1);
            newToken = destination.startsWith("\"")
                    ? quotedToken(destination, 0).value()
                    : destination;
        }
        return new PathPair(stripPrefix(oldToken, "a/"), stripPrefix(newToken, "b/"));
    }

    private static String parseMarkerPath(String token, String prefix) {
        String decoded = decodePath(token);
        if ("/dev/null".equals(decoded)) return null;
        return stripPrefix(decoded, prefix);
    }

    private static String decodePath(String token) {
        String value = token.strip();
        if (value.startsWith("\"")) return quotedToken(value, 0).value();
        int timestamp = value.indexOf('\t');
        return timestamp >= 0 ? value.substring(0, timestamp) : value;
    }

    private static Token quotedToken(String input, int start) {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        int index = start + 1;
        while (index < input.length()) {
            char current = input.charAt(index++);
            if (current == '"') {
                return new Token(decodeUtf8(bytes.toByteArray()), index);
            }
            if (current != '\\') {
                bytes.writeBytes(String.valueOf(current).getBytes(StandardCharsets.UTF_8));
                continue;
            }
            if (index >= input.length()) break;
            char escaped = input.charAt(index++);
            if (escaped >= '0' && escaped <= '7') {
                int value = escaped - '0';
                int digits = 1;
                while (digits < 3 && index < input.length()
                        && input.charAt(index) >= '0' && input.charAt(index) <= '7') {
                    value = value * 8 + input.charAt(index++) - '0';
                    digits++;
                }
                bytes.write(value);
            } else {
                int decoded = switch (escaped) {
                    case 'n' -> '\n';
                    case 'r' -> '\r';
                    case 't' -> '\t';
                    case 'b' -> '\b';
                    case 'f' -> '\f';
                    case 'v' -> 0x0b;
                    case 'a' -> 0x07;
                    case '\\', '"' -> escaped;
                    default -> throw new IllegalArgumentException(
                            "Unsupported escape in exact diff path: \\" + escaped);
                };
                bytes.write(decoded);
            }
        }
        throw new IllegalArgumentException("Unterminated quoted path in exact diff");
    }

    private static String decodeUtf8(byte[] bytes) {
        try {
            return StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(bytes))
                    .toString();
        } catch (CharacterCodingException failure) {
            throw new IllegalArgumentException("Malformed UTF-8 in exact diff path", failure);
        }
    }

    private static int skipSpaces(String value, int index) {
        while (index < value.length() && Character.isWhitespace(value.charAt(index))) index++;
        return index;
    }

    private static String stripPrefix(String path, String prefix) {
        if (path == null || !path.startsWith(prefix) || path.length() == prefix.length()) {
            throw new IllegalArgumentException("Exact diff path must start with " + prefix);
        }
        return path.substring(prefix.length());
    }

    private record Token(String value, int end) {}
    private record PathPair(String oldPath, String newPath) {}
}

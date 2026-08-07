package org.rostilos.codecrow.scmevidence.service;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

final class UnifiedDiffAddedLineParser {
    private static final Pattern TARGET_FILE = Pattern.compile("^\\+\\+\\+ b/(.+)$");
    private static final Pattern HUNK = Pattern.compile(
            "^@@ -\\d+(?:,\\d+)? \\+(\\d+)(?:,(\\d+))? @@.*$");

    record AddedLine(String filePath, int lineNumber, String lineHash) {}

    List<AddedLine> parse(String diff) {
        List<AddedLine> result = new ArrayList<>();
        String file = null;
        int newLine = -1;
        for (String line : (diff == null ? "" : diff.replace("\r\n", "\n")).split("\n", -1)) {
            Matcher fileMatcher = TARGET_FILE.matcher(line);
            if (fileMatcher.matches()) {
                file = fileMatcher.group(1);
                continue;
            }
            Matcher hunkMatcher = HUNK.matcher(line);
            if (hunkMatcher.matches()) {
                newLine = Integer.parseInt(hunkMatcher.group(1));
                continue;
            }
            if (file == null || newLine < 0 || line.startsWith("\\ No newline")) {
                continue;
            }
            if (line.startsWith("+") && !line.startsWith("+++")) {
                result.add(new AddedLine(file, newLine,
                        PatchIdentity.lineSha256(line.substring(1))));
                newLine++;
            } else if (!line.startsWith("-")) {
                newLine++;
            }
        }
        return result;
    }
}

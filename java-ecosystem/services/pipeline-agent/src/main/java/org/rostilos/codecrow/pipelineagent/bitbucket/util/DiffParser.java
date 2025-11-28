package org.rostilos.codecrow.pipelineagent.bitbucket.util;

import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Utility for parsing unified diff format to extract changed files and code snippets.
 * Used for RAG context enrichment.
 */
public class DiffParser {
    
    private static final Pattern DIFF_GIT_PATTERN = Pattern.compile("^diff --git\\s+a/(\\S+)\\s+b/(\\S+)");
    private static final Pattern FUNCTION_PATTERN_JAVA = Pattern.compile("^\\s*(public|private|protected)?\\s*(static)?\\s*\\w+\\s+\\w+\\s*\\([^)]*\\)");
    private static final Pattern FUNCTION_PATTERN_PYTHON = Pattern.compile("^\\s*def\\s+\\w+\\s*\\([^)]*\\)");
    private static final Pattern FUNCTION_PATTERN_JS = Pattern.compile("^\\s*(function\\s+\\w+\\s*\\([^)]*\\)|const\\s+\\w+\\s*=\\s*\\([^)]*\\)\\s*=>)");
    private static final Pattern CLASS_PATTERN = Pattern.compile("^\\s*(class|interface)\\s+\\w+");

    /**
     * Information about a changed file from diff.
     */
    public static class DiffFileInfo {
        private final String path;
        private final String changeType;
        private final List<String> codeSnippets;
        
        public DiffFileInfo(String path, String changeType, List<String> codeSnippets) {
            this.path = path;
            this.changeType = changeType;
            this.codeSnippets = codeSnippets;
        }
        
        public String getPath() { return path; }
        public String getChangeType() { return changeType; }
        public List<String> getCodeSnippets() { return codeSnippets; }
    }

    /**
     * Parse unified diff and extract file information.
     *
     * @param rawDiff Unified diff content
     * @param maxSnippetsPerFile Maximum code snippets to extract per file
     * @return List of parsed file information
     */
    public static List<DiffFileInfo> parseDiff(String rawDiff, int maxSnippetsPerFile) {
        if (rawDiff == null || rawDiff.isBlank()) {
            return Collections.emptyList();
        }

        List<DiffFileInfo> files = new ArrayList<>();
        String[] lines = rawDiff.split("\\r?\\n");
        
        String currentFilePath = null;
        String changeType = "modified";
        List<String> addedLines = new ArrayList<>();
        
        for (String line : lines) {
            // New file diff section
            Matcher diffMatcher = DIFF_GIT_PATTERN.matcher(line);
            if (diffMatcher.find()) {
                // Save previous file
                if (currentFilePath != null) {
                    List<String> snippets = extractSignificantSnippets(addedLines, maxSnippetsPerFile);
                    files.add(new DiffFileInfo(currentFilePath, changeType, snippets));
                }
                
                // Start new file
                currentFilePath = diffMatcher.group(2);
                changeType = "modified";
                addedLines.clear();
                continue;
            }
            
            // Detect change type
            if (currentFilePath != null) {
                if (line.startsWith("new file mode")) {
                    changeType = "added";
                } else if (line.startsWith("deleted file mode")) {
                    changeType = "deleted";
                } else if (line.startsWith("rename from")) {
                    changeType = "renamed";
                }
                
                // Extract added lines (excluding marker lines)
                if (line.startsWith("+") && !line.startsWith("+++")) {
                    String content = line.substring(1).trim();
                    if (!content.isEmpty()) {
                        addedLines.add(content);
                    }
                }
            }
        }
        
        // Save last file
        if (currentFilePath != null) {
            List<String> snippets = extractSignificantSnippets(addedLines, maxSnippetsPerFile);
            files.add(new DiffFileInfo(currentFilePath, changeType, snippets));
        }
        
        return files;
    }

    /**
     * Extract changed file paths from diff.
     */
    public static List<String> extractChangedFiles(String rawDiff) {
        List<DiffFileInfo> diffFiles = parseDiff(rawDiff, 0);
        List<String> paths = new ArrayList<>();
        for (DiffFileInfo file : diffFiles) {
            if (!"deleted".equals(file.getChangeType())) {
                paths.add(file.getPath());
            }
        }
        return paths;
    }

    /**
     * Extract representative code snippets from all changed files.
     * These are used for semantic search in RAG.
     */
    public static List<String> extractDiffSnippets(String rawDiff, int maxTotalSnippets) {
        List<DiffFileInfo> diffFiles = parseDiff(rawDiff, 3);
        List<String> allSnippets = new ArrayList<>();
        
        for (DiffFileInfo file : diffFiles) {
            allSnippets.addAll(file.getCodeSnippets());
            if (allSnippets.size() >= maxTotalSnippets) {
                break;
            }
        }
        
        return allSnippets.subList(0, Math.min(allSnippets.size(), maxTotalSnippets));
    }

    /**
     * Extract significant code snippets from changed lines.
     * Prioritizes function/class definitions and meaningful changes.
     */
    private static List<String> extractSignificantSnippets(List<String> lines, int maxSnippets) {
        if (maxSnippets == 0) {
            return Collections.emptyList();
        }
        
        List<String> snippets = new ArrayList<>();
        
        // First pass: look for function/class signatures
        for (String line : lines) {
            if (line.trim().isEmpty() || line.trim().startsWith("//") || line.trim().startsWith("#")) {
                continue;
            }
            
            if (isSignificantLine(line)) {
                String snippet = line.length() > 150 ? line.substring(0, 150) : line;
                snippets.add(snippet);
                
                if (snippets.size() >= maxSnippets) {
                    return snippets;
                }
            }
        }
        
        // Second pass: if no significant lines, take first meaningful non-empty lines
        if (snippets.isEmpty()) {
            for (String line : lines) {
                String trimmed = line.trim();
                if (!trimmed.isEmpty() && trimmed.length() > 10) {
                    String snippet = trimmed.length() > 150 ? trimmed.substring(0, 150) : trimmed;
                    snippets.add(snippet);
                    
                    if (snippets.size() >= maxSnippets) {
                        break;
                    }
                }
            }
        }
        
        return snippets;
    }

    /**
     * Check if a line contains significant code (function/class definition).
     */
    private static boolean isSignificantLine(String line) {
        return FUNCTION_PATTERN_JAVA.matcher(line).find() ||
               FUNCTION_PATTERN_PYTHON.matcher(line).find() ||
               FUNCTION_PATTERN_JS.matcher(line).find() ||
               CLASS_PATTERN.matcher(line).find();
    }
}


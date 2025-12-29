package org.rostilos.codecrow.analysisengine.util;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Utility class for filtering large files from diff content.
 * 
 * Mirrors the behavior of org.rostilos.codecrow.mcp.filter.LargeContentFilter
 * from vcs-mcp module to ensure consistent filtering across the system.
 * TODO: reuse it in mcp, DRY pattern + use threshold bytes from configuration
 */
public class DiffContentFilter {
    
    private static final Logger log = LoggerFactory.getLogger(DiffContentFilter.class);
    
    /**
     * Default size threshold in bytes (25KB) - same as LargeContentFilter
     */
    public static final int DEFAULT_SIZE_THRESHOLD_BYTES = 25 * 1024;
    
    /**
     * Placeholder template for filtered diff (matches LargeContentFilter format)
     */
    private static final String FILTERED_DIFF_TEMPLATE = """
            diff --git a/%s b/%s
            --- a/%s
            +++ b/%s
            [CodeCrow Filter: file diff too large (>%dKB), omitted from analysis. File type: %s]
            """;
    
    private static final Pattern DIFF_GIT_PATTERN = Pattern.compile("^diff --git\\s+a/(\\S+)\\s+b/(\\S+)");
    
    private final int sizeThresholdBytes;
    
    public DiffContentFilter() {
        this(DEFAULT_SIZE_THRESHOLD_BYTES);
    }
    
    public DiffContentFilter(int sizeThresholdBytes) {
        this.sizeThresholdBytes = sizeThresholdBytes;
    }
    
    /**
     * Filter a raw diff string, replacing large file diffs with placeholders.
     * 
     * @param rawDiff The raw diff string from VCS
     * @return Filtered diff with large files replaced by placeholders
     */
    public String filterDiff(String rawDiff) {
        if (rawDiff == null || rawDiff.isEmpty()) {
            return rawDiff;
        }
        
        // Check if the entire diff is small enough
        if (rawDiff.getBytes().length <= sizeThresholdBytes) {
            return rawDiff;
        }
        
        // Parse and filter individual file diffs
        List<FileDiffSection> sections = parseFileSections(rawDiff);
        
        if (sections.isEmpty()) {
            // If parsing failed, check size and return placeholder if too large
            if (rawDiff.getBytes().length > sizeThresholdBytes) {
                log.info("Filtering entire diff as unparseable and too large ({} bytes)", rawDiff.getBytes().length);
                return "[CodeCrow Filter: diff too large and unparseable, omitted from analysis]\nOriginal size: " 
                        + rawDiff.getBytes().length + " bytes";
            }
            return rawDiff;
        }
        
        StringBuilder filteredDiff = new StringBuilder();
        int filteredCount = 0;
        int thresholdKb = sizeThresholdBytes / 1024;
        
        for (FileDiffSection section : sections) {
            int sectionSize = section.content.getBytes().length;
            
            if (sectionSize > sizeThresholdBytes) {
                // Replace with placeholder
                String placeholder = String.format(FILTERED_DIFF_TEMPLATE,
                        section.filePath, section.filePath,
                        section.filePath, section.filePath,
                        thresholdKb, section.changeType);
                filteredDiff.append(placeholder);
                filteredCount++;
                log.info("Filtered large file diff: {} ({} bytes)", section.filePath, sectionSize);
            } else {
                filteredDiff.append(section.content);
                if (!section.content.endsWith("\n")) {
                    filteredDiff.append("\n");
                }
            }
        }
        
        if (filteredCount > 0) {
            log.info("Filtered {} large file(s) from diff, total sections: {}", filteredCount, sections.size());
        }
        
        return filteredDiff.toString();
    }
    
    /**
     * Parse diff into individual file sections.
     */
    private List<FileDiffSection> parseFileSections(String rawDiff) {
        List<FileDiffSection> sections = new ArrayList<>();
        String[] lines = rawDiff.split("\n");
        
        StringBuilder currentContent = new StringBuilder();
        String currentFilePath = null;
        String currentChangeType = "MODIFIED";
        
        for (String line : lines) {
            Matcher diffMatcher = DIFF_GIT_PATTERN.matcher(line);
            
            if (diffMatcher.find()) {
                // Save previous section
                if (currentFilePath != null && currentContent.length() > 0) {
                    sections.add(new FileDiffSection(currentFilePath, currentChangeType, currentContent.toString()));
                }
                
                // Start new section
                currentFilePath = diffMatcher.group(2);
                currentChangeType = "MODIFIED";
                currentContent = new StringBuilder();
                currentContent.append(line).append("\n");
            } else if (currentFilePath != null) {
                currentContent.append(line).append("\n");
                
                // Detect change type
                if (line.startsWith("new file mode")) {
                    currentChangeType = "ADDED";
                } else if (line.startsWith("deleted file mode")) {
                    currentChangeType = "DELETED";
                } else if (line.startsWith("rename from")) {
                    currentChangeType = "RENAMED";
                } else if (line.startsWith("Binary files")) {
                    currentChangeType = "BINARY";
                }
            }
        }
        
        // Save last section
        if (currentFilePath != null && currentContent.length() > 0) {
            sections.add(new FileDiffSection(currentFilePath, currentChangeType, currentContent.toString()));
        }
        
        return sections;
    }
    
    /**
     * Get the size threshold in bytes.
     */
    public int getSizeThresholdBytes() {
        return sizeThresholdBytes;
    }
    
    /**
     * Internal class to hold parsed diff section.
     */
    private static class FileDiffSection {
        final String filePath;
        final String changeType;
        final String content;
        
        FileDiffSection(String filePath, String changeType, String content) {
            this.filePath = filePath;
            this.changeType = changeType;
            this.content = content;
        }
    }
}

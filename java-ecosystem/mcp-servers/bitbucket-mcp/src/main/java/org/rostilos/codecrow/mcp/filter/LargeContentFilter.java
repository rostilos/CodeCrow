package org.rostilos.codecrow.mcp.filter;

import org.rostilos.codecrow.mcp.bitbucket.pullrequest.diff.FileDiff;
import org.rostilos.codecrow.mcp.bitbucket.pullrequest.diff.RawDiffParser;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.List;

/**
 * Utility class for filtering large files from VCS content to reduce token usage.
 * Files exceeding the size threshold are replaced with a placeholder message.
 */
public class LargeContentFilter {
    
    private static final Logger log = LoggerFactory.getLogger(LargeContentFilter.class);
    
    /**
     * Default size threshold in bytes (25KB)
     */
    public static final int DEFAULT_SIZE_THRESHOLD_BYTES = 25 * 1024;
    
    /**
     * Placeholder message for filtered content
     */
    public static final String FILTERED_PLACEHOLDER = "[CodeCrow Filter: file too large (>25KB), omitted from analysis]";
    
    /**
     * Placeholder template for filtered diff with metadata
     */
    private static final String FILTERED_DIFF_TEMPLATE = """
            diff --git a/%s b/%s
            --- a/%s
            +++ b/%s
            [CodeCrow Filter: file diff too large (>25KB), omitted from analysis. File type: %s]
            """;
    
    private final int sizeThresholdBytes;
    private final RawDiffParser diffParser;
    
    public LargeContentFilter() {
        this(DEFAULT_SIZE_THRESHOLD_BYTES);
    }
    
    public LargeContentFilter(int sizeThresholdBytes) {
        this.sizeThresholdBytes = sizeThresholdBytes;
        this.diffParser = new RawDiffParser();
    }
    
    /**
     * Filter file content if it exceeds the size threshold.
     * 
     * @param content The file content to filter
     * @param filePath The path of the file (for logging purposes)
     * @return The original content if under threshold, or a placeholder message if too large
     */
    public String filterFileContent(String content, String filePath) {
        if (content == null) {
            return null;
        }
        
        int contentSize = content.getBytes().length;
        if (contentSize > sizeThresholdBytes) {
            log.info("Filtering large file content: {} ({} bytes > {} bytes threshold)", 
                    filePath, contentSize, sizeThresholdBytes);
            return FILTERED_PLACEHOLDER + "\nOriginal size: " + contentSize + " bytes\nFile: " + filePath;
        }
        
        return content;
    }
    
    /**
     * Filter a raw diff string, replacing large file diffs with placeholders.
     * Preserves file metadata (paths, diff type) but removes the actual diff content.
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
        
        // Parse the diff into individual file diffs
        List<FileDiff> fileDiffs = diffParser.execute(rawDiff);
        
        if (fileDiffs.isEmpty()) {
            // If parsing failed, check size and return placeholder if too large
            if (rawDiff.getBytes().length > sizeThresholdBytes) {
                log.info("Filtering entire diff as unparseable and too large ({} bytes)", rawDiff.getBytes().length);
                return "[CodeCrow Filter: diff too large and unparseable, omitted from analysis]\nOriginal size: " + rawDiff.getBytes().length + " bytes";
            }
            return rawDiff;
        }
        
        // Filter each file diff
        StringBuilder filteredDiff = new StringBuilder();
        int filteredCount = 0;
        
        for (FileDiff fileDiff : fileDiffs) {
            String changes = fileDiff.getChanges();
            int fileSize = changes != null ? changes.getBytes().length : 0;
            
            if (fileSize > sizeThresholdBytes) {
                // Replace with placeholder, keeping metadata
                String diffType = fileDiff.getDiffType() != null ? fileDiff.getDiffType().name() : "MODIFIED";
                String filePath = fileDiff.getFilePath();
                filteredDiff.append(String.format(FILTERED_DIFF_TEMPLATE, 
                        filePath, filePath, filePath, filePath, diffType));
                filteredCount++;
                log.info("Filtered large file diff: {} ({} bytes, type: {})", filePath, fileSize, diffType);
            } else {
                // Keep original diff content
                filteredDiff.append(changes);
            }
        }
        
        if (filteredCount > 0) {
            log.info("Filtered {} large file(s) from diff", filteredCount);
        }
        
        return filteredDiff.toString();
    }
    
    /**
     * Check if content exceeds the size threshold.
     * 
     * @param content The content to check
     * @return true if content is too large
     */
    public boolean isContentTooLarge(String content) {
        return content != null && content.getBytes().length > sizeThresholdBytes;
    }
    
    /**
     * Get the size threshold in bytes.
     */
    public int getSizeThresholdBytes() {
        return sizeThresholdBytes;
    }
}

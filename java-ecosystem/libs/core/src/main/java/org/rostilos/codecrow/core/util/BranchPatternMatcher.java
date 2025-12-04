package org.rostilos.codecrow.core.util;

import java.util.List;
import java.util.regex.Pattern;

/**
 * Utility class for matching branch names against patterns.
 * Supports exact matches and glob patterns with wildcards:
 * - "*" matches any sequence of characters except "/"
 * - "**" matches any sequence of characters including "/"
 * 
 * Examples:
 * - "main" matches only "main"
 * - "release/*" matches "release/1.0" but not "release/1.0/hotfix"
 * - "feature/**" matches "feature/foo" and "feature/foo/bar"
 */
public final class BranchPatternMatcher {
    
    private BranchPatternMatcher() {
        // Utility class - prevent instantiation
    }
    
    /**
     * Check if a branch name matches any of the given patterns.
     * 
     * @param branchName the branch name to check
     * @param patterns the list of patterns to match against
     * @return true if the branch matches at least one pattern, false otherwise
     */
    public static boolean matchesAny(String branchName, List<String> patterns) {
        if (branchName == null || patterns == null || patterns.isEmpty()) {
            return false;
        }
        
        for (String pattern : patterns) {
            if (matches(branchName, pattern)) {
                return true;
            }
        }
        return false;
    }
    
    /**
     * Check if a branch name matches a single pattern.
     * 
     * @param branchName the branch name to check
     * @param pattern the pattern to match against
     * @return true if the branch matches the pattern
     */
    public static boolean matches(String branchName, String pattern) {
        if (branchName == null || pattern == null) {
            return false;
        }
        
        // Exact match (no wildcards)
        if (!pattern.contains("*")) {
            return branchName.equals(pattern);
        }
        
        // Convert glob pattern to regex
        String regex = globToRegex(pattern);
        return Pattern.matches(regex, branchName);
    }
    
    /**
     * Convert a glob pattern to a regex pattern.
     * - "**" -> ".*" (match any characters including /)
     * - "*" -> "[^/]*" (match any characters except /)
     * - Other regex special chars are escaped
     */
    private static String globToRegex(String glob) {
        StringBuilder regex = new StringBuilder();
        regex.append("^");
        
        int i = 0;
        while (i < glob.length()) {
            char c = glob.charAt(i);
            
            if (c == '*') {
                if (i + 1 < glob.length() && glob.charAt(i + 1) == '*') {
                    // "**" matches any characters including /
                    regex.append(".*");
                    i += 2;
                } else {
                    // "*" matches any characters except /
                    regex.append("[^/]*");
                    i++;
                }
            } else if (c == '?') {
                // "?" matches any single character except /
                regex.append("[^/]");
                i++;
            } else if ("\\^$.|+()[]{}".indexOf(c) >= 0) {
                // Escape regex special characters
                regex.append("\\").append(c);
                i++;
            } else {
                regex.append(c);
                i++;
            }
        }
        
        regex.append("$");
        return regex.toString();
    }
    
    /**
     * Check if the patterns configuration allows analyzing a branch.
     * If patterns is null or empty, all branches are allowed (no filtering).
     * 
     * @param branchName the branch name to check
     * @param patterns the list of allowed patterns (null/empty means allow all)
     * @return true if analysis should proceed, false if it should be skipped
     */
    public static boolean shouldAnalyze(String branchName, List<String> patterns) {
        // If no patterns configured, allow all branches
        if (patterns == null || patterns.isEmpty()) {
            return true;
        }
        
        return matchesAny(branchName, patterns);
    }
}

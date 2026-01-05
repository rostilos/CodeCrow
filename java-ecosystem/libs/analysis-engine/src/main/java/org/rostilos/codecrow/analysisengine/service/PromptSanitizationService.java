package org.rostilos.codecrow.analysisengine.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Service for sanitizing user input in /codecrow ask commands to prevent prompt injection attacks.
 * 
 * Security considerations:
 * - Removes or escapes potentially dangerous patterns
 * - Limits input length
 * - Detects and blocks common prompt injection techniques
 */
@Service
public class PromptSanitizationService {
    
    private static final Logger log = LoggerFactory.getLogger(PromptSanitizationService.class);
    
    /** Maximum allowed length for user questions */
    private static final int MAX_QUESTION_LENGTH = 2000;
    
    /** Patterns that indicate potential prompt injection attempts */
    private static final List<Pattern> INJECTION_PATTERNS = List.of(
        // System prompt overrides
        Pattern.compile("(?i)\\b(ignore|disregard|forget)\\s+(all\\s+)?(previous|above|prior)\\s+(instructions?|prompts?|rules?)"),
        Pattern.compile("(?i)\\b(new|different|change)\\s+(system\\s+)?(prompt|instructions?|rules?)"),
        Pattern.compile("(?i)\\byou\\s+are\\s+(now|a)\\b"),
        Pattern.compile("(?i)\\b(act|behave|pretend)\\s+(as|like)\\s+"),
        Pattern.compile("(?i)\\brole\\s*[:=]"),
        
        // Output format manipulation
        Pattern.compile("(?i)\\b(output|print|write|return)\\s+(only|just|exactly)"),
        Pattern.compile("(?i)\\bformat\\s*[:=]\\s*(json|xml|raw)"),
        
        // Delimiter injection
        Pattern.compile("(?i)###\\s*(system|assistant|user)"),
        Pattern.compile("(?i)<\\|?(system|assistant|user|endof)\\|?>"),
        Pattern.compile("(?i)\\[\\s*(INST|SYS)\\s*\\]"),
        
        // Data extraction attempts
        Pattern.compile("(?i)\\b(reveal|show|display|tell)\\s+(me\\s+)?(your|the)\\s+(system|initial|original)\\s+(prompt|instructions?)"),
        Pattern.compile("(?i)\\bwhat\\s+(are|is)\\s+your\\s+(system\\s+)?(prompt|instructions?|rules?)"),
        
        // Code execution patterns
        Pattern.compile("(?i)\\bexec(ute)?\\s*\\("),
        Pattern.compile("(?i)\\beval\\s*\\("),
        Pattern.compile("(?i)\\bos\\s*\\.\\s*(system|popen|exec)"),
        Pattern.compile("(?i)\\bsubprocess\\s*\\."),
        Pattern.compile("(?i)\\bimport\\s+(os|sys|subprocess)")
    );
    
    /** Patterns for suspicious markdown/code blocks that might contain injection */
    private static final Pattern CODE_BLOCK_PATTERN = Pattern.compile("```[\\s\\S]*?```");

    /**
     * Result of sanitization operation.
     */
    public record SanitizationResult(
        boolean safe,
        String sanitizedInput,
        String reason
    ) {
        public static SanitizationResult safe(String input) {
            return new SanitizationResult(true, input, null);
        }
        
        public static SanitizationResult blocked(String reason) {
            return new SanitizationResult(false, null, reason);
        }
        
        public static SanitizationResult modified(String sanitizedInput, String reason) {
            return new SanitizationResult(true, sanitizedInput, reason);
        }
    }
    
    /**
     * Sanitize a user question for the /codecrow ask command.
     * 
     * @param input The raw user input
     * @return Sanitization result with safe status and sanitized input
     */
    public SanitizationResult sanitize(String input) {
        if (input == null || input.isBlank()) {
            return SanitizationResult.blocked("Empty input");
        }
        
        String trimmed = input.trim();
        
        // Check length
        if (trimmed.length() > MAX_QUESTION_LENGTH) {
            log.warn("Question exceeds maximum length: {} chars", trimmed.length());
            return SanitizationResult.blocked(
                String.format("Question too long. Maximum length is %d characters.", MAX_QUESTION_LENGTH)
            );
        }
        
        // Check for injection patterns
        for (Pattern pattern : INJECTION_PATTERNS) {
            Matcher matcher = pattern.matcher(trimmed);
            if (matcher.find()) {
                log.warn("Potential prompt injection detected: pattern={}", pattern.pattern());
                return SanitizationResult.blocked(
                    "Your question contains patterns that are not allowed for security reasons."
                );
            }
        }
        
        // Sanitize the input
        String sanitized = sanitizeContent(trimmed);
        
        // Check if content was significantly modified
        if (!sanitized.equals(trimmed)) {
            log.info("Question was sanitized, original length: {}, sanitized length: {}", 
                trimmed.length(), sanitized.length());
            return SanitizationResult.modified(sanitized, "Some content was sanitized for security");
        }
        
        return SanitizationResult.safe(sanitized);
    }
    
    /**
     * Sanitize content by removing or escaping potentially dangerous patterns.
     */
    private String sanitizeContent(String input) {
        String result = input;
        
        // Remove excessive whitespace
        result = result.replaceAll("\\s{3,}", "  ");
        
        // Escape delimiter-like sequences
        result = result.replace("###", "# # #");
        result = result.replace("<|", "< |");
        result = result.replace("|>", "| >");
        
        // Remove null bytes and control characters (except newlines and tabs)
        result = result.replaceAll("[\\x00-\\x08\\x0B\\x0C\\x0E-\\x1F]", "");
        
        // Neutralize code blocks that might contain injection
        result = neutralizeCodeBlocks(result);
        
        return result.trim();
    }
    
    /**
     * Neutralize potentially dangerous code blocks by marking them as user content.
     */
    private String neutralizeCodeBlocks(String input) {
        // For code blocks, we prepend a comment indicating it's user-provided
        return CODE_BLOCK_PATTERN.matcher(input).replaceAll(match -> {
            String block = match.group();
            // Check if the block contains suspicious patterns
            for (Pattern pattern : INJECTION_PATTERNS) {
                if (pattern.matcher(block).find()) {
                    return "[Code block removed for security]";
                }
            }
            return block;
        });
    }
    
    /**
     * Extract and validate issue references from a question.
     * Valid formats:
     * - #123 (issue number)
     * - HIGH-1, MEDIUM-2, LOW-3 (severity-prefixed)
     * - issue:123 (explicit)
     * 
     * @param question The user question
     * @return List of extracted issue references
     */
    public List<IssueReference> extractIssueReferences(String question) {
        if (question == null) {
            return List.of();
        }
        
        List<IssueReference> references = new java.util.ArrayList<>();
        
        // Pattern for #123 style references
        Pattern hashPattern = Pattern.compile("#(\\d+)\\b");
        Matcher hashMatcher = hashPattern.matcher(question);
        while (hashMatcher.find()) {
            references.add(new IssueReference(
                IssueReferenceType.NUMBER, 
                hashMatcher.group(1),
                null
            ));
        }
        
        // Pattern for SEVERITY-NUMBER style (e.g., HIGH-1, MEDIUM-2)
        Pattern severityPattern = Pattern.compile("\\b(HIGH|MEDIUM|LOW)-(\\d+)\\b", Pattern.CASE_INSENSITIVE);
        Matcher severityMatcher = severityPattern.matcher(question);
        while (severityMatcher.find()) {
            references.add(new IssueReference(
                IssueReferenceType.SEVERITY_INDEX,
                severityMatcher.group(2),
                severityMatcher.group(1).toUpperCase()
            ));
        }
        
        // Pattern for explicit issue:123 references
        Pattern explicitPattern = Pattern.compile("\\bissue:(\\d+)\\b", Pattern.CASE_INSENSITIVE);
        Matcher explicitMatcher = explicitPattern.matcher(question);
        while (explicitMatcher.find()) {
            references.add(new IssueReference(
                IssueReferenceType.EXPLICIT,
                explicitMatcher.group(1),
                null
            ));
        }
        
        return references;
    }
    
    /**
     * Types of issue references that can be parsed from user questions.
     */
    public enum IssueReferenceType {
        /** Simple number reference like #123 */
        NUMBER,
        /** Severity-prefixed reference like HIGH-1 */
        SEVERITY_INDEX,
        /** Explicit reference like issue:123 */
        EXPLICIT
    }
    
    /**
     * Represents a parsed issue reference from user input.
     */
    public record IssueReference(
        IssueReferenceType type,
        String identifier,
        String severity
    ) {}
}

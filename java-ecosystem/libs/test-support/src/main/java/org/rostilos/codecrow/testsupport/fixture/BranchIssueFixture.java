package org.rostilos.codecrow.testsupport.fixture;

/**
 * Builder for creating test BranchIssue entities / data maps.
 */
public final class BranchIssueFixture {

    private String ruleId = "TEST_RULE_001";
    private String message = "Test issue description";
    private String filePath = "src/main/java/Example.java";
    private Integer lineNumber = 10;
    private Integer currentLineNumber = 10;
    private String codeSnippet = "public void example() {";
    private String lineHash = null;
    private String currentLineHash = null;
    private String severity = "MAJOR";
    private String category = "BUG";
    private String scope = "LINE";
    private String contentFingerprint = null;
    private String detectionSource = "AI";

    private BranchIssueFixture() {
    }

    public static BranchIssueFixture aBranchIssue() {
        return new BranchIssueFixture();
    }

    public BranchIssueFixture withRuleId(String ruleId) { this.ruleId = ruleId; return this; }
    public BranchIssueFixture withMessage(String message) { this.message = message; return this; }
    public BranchIssueFixture withFilePath(String filePath) { this.filePath = filePath; return this; }
    public BranchIssueFixture withLineNumber(Integer lineNumber) { this.lineNumber = lineNumber; return this; }
    public BranchIssueFixture withCurrentLineNumber(Integer currentLineNumber) { this.currentLineNumber = currentLineNumber; return this; }
    public BranchIssueFixture withCodeSnippet(String codeSnippet) { this.codeSnippet = codeSnippet; return this; }
    public BranchIssueFixture withLineHash(String lineHash) { this.lineHash = lineHash; return this; }
    public BranchIssueFixture withCurrentLineHash(String currentLineHash) { this.currentLineHash = currentLineHash; return this; }
    public BranchIssueFixture withSeverity(String severity) { this.severity = severity; return this; }
    public BranchIssueFixture withCategory(String category) { this.category = category; return this; }
    public BranchIssueFixture withScope(String scope) { this.scope = scope; return this; }
    public BranchIssueFixture withContentFingerprint(String fp) { this.contentFingerprint = fp; return this; }
    public BranchIssueFixture withDetectionSource(String ds) { this.detectionSource = ds; return this; }

    public java.util.Map<String, Object> asMap() {
        var map = new java.util.LinkedHashMap<String, Object>();
        map.put("ruleId", ruleId);
        map.put("message", message);
        map.put("filePath", filePath);
        map.put("lineNumber", lineNumber);
        map.put("currentLineNumber", currentLineNumber);
        map.put("codeSnippet", codeSnippet);
        map.put("lineHash", lineHash);
        map.put("currentLineHash", currentLineHash);
        map.put("severity", severity);
        map.put("category", category);
        map.put("scope", scope);
        map.put("contentFingerprint", contentFingerprint);
        map.put("detectionSource", detectionSource);
        return map;
    }

    public String getRuleId() { return ruleId; }
    public String getMessage() { return message; }
    public String getFilePath() { return filePath; }
    public Integer getLineNumber() { return lineNumber; }
    public Integer getCurrentLineNumber() { return currentLineNumber; }
    public String getCodeSnippet() { return codeSnippet; }
    public String getSeverity() { return severity; }
    public String getCategory() { return category; }
}

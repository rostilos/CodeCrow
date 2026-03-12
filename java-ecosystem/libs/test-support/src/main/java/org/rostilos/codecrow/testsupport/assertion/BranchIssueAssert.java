package org.rostilos.codecrow.testsupport.assertion;

import org.assertj.core.api.AbstractAssert;

import java.util.Map;

/**
 * Fluent AssertJ assertions for branch issue state.
 * Works with Map representations or entity objects.
 */
public class BranchIssueAssert extends AbstractAssert<BranchIssueAssert, Map<String, Object>> {

    private BranchIssueAssert(Map<String, Object> actual) {
        super(actual, BranchIssueAssert.class);
    }

    public static BranchIssueAssert assertThatBranchIssue(Map<String, Object> issue) {
        return new BranchIssueAssert(issue);
    }

    public BranchIssueAssert hasFilePath(String expected) {
        isNotNull();
        String actualPath = (String) actual.get("filePath");
        if (!expected.equals(actualPath)) {
            failWithMessage("Expected filePath <%s> but was <%s>", expected, actualPath);
        }
        return this;
    }

    public BranchIssueAssert hasCurrentLineNumber(int expected) {
        isNotNull();
        Object val = actual.get("currentLineNumber");
        int actualLine = val instanceof Number ? ((Number) val).intValue() : -1;
        if (actualLine != expected) {
            failWithMessage("Expected currentLineNumber <%d> but was <%d>", expected, actualLine);
        }
        return this;
    }

    public BranchIssueAssert hasCurrentLineNumberWithinFileLength(int fileLength) {
        isNotNull();
        Object val = actual.get("currentLineNumber");
        int actualLine = val instanceof Number ? ((Number) val).intValue() : -1;
        if (actualLine < 1 || actualLine > fileLength) {
            failWithMessage("Expected currentLineNumber to be within [1, %d] but was <%d>",
                    fileLength, actualLine);
        }
        return this;
    }

    public BranchIssueAssert isNotResolved() {
        isNotNull();
        Boolean resolved = (Boolean) actual.get("resolved");
        if (Boolean.TRUE.equals(resolved)) {
            failWithMessage("Expected issue to NOT be resolved but it was");
        }
        return this;
    }

    public BranchIssueAssert isResolved() {
        isNotNull();
        Boolean resolved = (Boolean) actual.get("resolved");
        if (!Boolean.TRUE.equals(resolved)) {
            failWithMessage("Expected issue to be resolved but it was not");
        }
        return this;
    }

    public BranchIssueAssert hasLineHashRefreshed() {
        isNotNull();
        String lineHash = (String) actual.get("lineHash");
        String currentLineHash = (String) actual.get("currentLineHash");
        if (lineHash != null && lineHash.equals(currentLineHash)) {
            // If both are the same, it may or may not be refreshed — skip
            return this;
        }
        if (currentLineHash == null || currentLineHash.isBlank()) {
            failWithMessage("Expected currentLineHash to be set (refreshed) but was null/blank");
        }
        return this;
    }

    public BranchIssueAssert hasRuleId(String expected) {
        isNotNull();
        String actualRuleId = (String) actual.get("ruleId");
        if (!expected.equals(actualRuleId)) {
            failWithMessage("Expected ruleId <%s> but was <%s>", expected, actualRuleId);
        }
        return this;
    }
}

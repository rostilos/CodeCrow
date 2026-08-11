package org.rostilos.codecrow.core.service.qadoc;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Splits rendered QA markdown into overview, test cases, and environment/setup notes. */
public final class QaDocContentParser {

    public static final String TEST_CASES_START = "<!-- codecrow-test-cases:start -->";
    public static final String TEST_CASES_END = "<!-- codecrow-test-cases:end -->";

    private static final Pattern SCENARIO_PATTERN = Pattern.compile(
            "(?m)^\\s*\\*\\*(.+?)\\*\\*\\s*\\((HIGH|MEDIUM|LOW)\\)[^\\r\\n]*$",
            Pattern.CASE_INSENSITIVE
    );
    private static final Pattern HEADING_PATTERN = Pattern.compile("(?m)^\\s*(#{2,6})\\s+(.+?)\\s*$");
    private static final Pattern LEGACY_SECTION_PATTERN = Pattern.compile(
            "(?im)^\\s*(#{2,4})\\s+(?:\\d+\\.\\s*)?Test Scenarios(?:\\s+by Area)?\\s*$"
    );
    private static final Pattern NUMBERED_SECTION_HEADING = Pattern.compile("^\\d+\\.\\s+.+$");
    private static final Pattern KNOWN_LATER_SECTION_HEADING = Pattern.compile(
            "^(?:Edge Cases(?: and Negative Testing)?|Negative Testing|Regression Risks|"
                    + "Environment(?: and Setup Notes)?|Setup Notes).*$",
            Pattern.CASE_INSENSITIVE
    );
    private static final Pattern ENVIRONMENT_SECTION_HEADING = Pattern.compile(
            "^(?:6\\.\\s+.+|(?:\\d+\\.\\s*)?(?:Environment and Setup Notes|"
                    + "Setup and Environment Notes|Environment Setup Notes|Environment Notes|Setup Notes))$",
            Pattern.CASE_INSENSITIVE
    );

    private QaDocContentParser() {
    }

    public static QaDocContent parse(String markdown) {
        String source = markdown == null ? "" : markdown.trim();
        Section section = findTestCaseSection(source);
        String withoutTestCases;
        List<QaDocTestCase> testCases;
        if (section == null) {
            withoutTestCases = source;
            testCases = parseTestCases(source);
        } else {
            withoutTestCases = (source.substring(0, section.start())
                    + "\n\n"
                    + source.substring(section.end()))
                    .replace(TEST_CASES_START, "")
                    .replace(TEST_CASES_END, "");
            testCases = parseTestCases(section.content());
        }

        Section environmentSection = findEnvironmentSection(withoutTestCases);
        String overview = environmentSection == null
                ? normalizeDocumentPart(withoutTestCases)
                : normalizeDocumentPart(
                        withoutTestCases.substring(0, environmentSection.start())
                                + "\n\n"
                                + withoutTestCases.substring(environmentSection.end()));
        String environment = environmentSection == null
                ? null
                : stripFirstHeading(environmentSection.content());

        return new QaDocContent(overview, testCases, normalizeNullablePart(environment));
    }

    /**
     * Parses only an explicitly marked section for unauthenticated disclosure.
     * Legacy heading inference is intentionally excluded from this boundary.
     */
    public static List<QaDocTestCase> parseMarkedTestCases(String markdown) {
        String source = markdown == null ? "" : markdown.trim();
        MarkedSection section = findMarkedSection(source);
        if (section == null) {
            return List.of();
        }
        return parseStructuredTestCases(
                source.substring(section.testContentStart(), section.testContentEnd()).trim());
    }

    /**
     * Keeps the complete rendered QA document while replacing only the marked
     * test-case body. The original section heading is retained so task comments
     * keep the same numbered structure as the generated document.
     */
    public static String replaceMarkedTestCases(String markdown, String replacementMarkdown) {
        String source = markdown == null ? "" : markdown.trim();
        String replacement = replacementMarkdown == null ? "" : replacementMarkdown.trim();
        if (replacement.isBlank()) {
            throw new IllegalArgumentException("Test-case replacement is required.");
        }

        MarkedSection section = findMarkedSection(source);
        if (section == null) {
            throw new IllegalArgumentException("A marked QA test-case section is required.");
        }

        String laterSections = source
                .substring(section.testContentEnd(), section.markerEnd())
                .trim();
        String afterMarker = source
                .substring(section.markerEnd() + TEST_CASES_END.length())
                .trim();

        StringBuilder result = new StringBuilder(source
                .substring(0, section.contentStart()).stripTrailing())
                .append("\n")
                .append(section.heading())
                .append("\n\n")
                .append(replacement)
                .append("\n")
                .append(TEST_CASES_END);
        if (!laterSections.isBlank()) {
            result.append("\n\n").append(laterSections);
        }
        if (!afterMarker.isBlank()) {
            result.append("\n\n").append(afterMarker);
        }
        return result.toString().trim();
    }

    private static Section findTestCaseSection(String markdown) {
        MarkedSection marked = findMarkedSection(markdown);
        if (marked != null) {
            int sectionEnd = marked.testContentEnd() == marked.markerEnd()
                    ? marked.markerEnd() + TEST_CASES_END.length()
                    : marked.testContentEnd();
            return new Section(
                    marked.markerStart(),
                    sectionEnd,
                    markdown.substring(marked.testContentStart(), marked.testContentEnd()).trim());
        }

        Matcher legacy = LEGACY_SECTION_PATTERN.matcher(markdown);
        if (!legacy.find()) {
            return null;
        }
        int headingLevel = legacy.group(1).length();
        int sectionEnd = markdown.length();
        Matcher headings = HEADING_PATTERN.matcher(markdown);
        headings.region(legacy.end(), markdown.length());
        while (headings.find()) {
            if (headings.group(1).length() <= headingLevel) {
                sectionEnd = headings.start();
                break;
            }
        }
        return new Section(legacy.start(), sectionEnd, markdown.substring(legacy.start(), sectionEnd).trim());
    }

    /**
     * Models occasionally put the closing marker after later peer sections.
     * Treat the next heading at the test-section level (or higher) as the real
     * boundary, while retaining the marker as the outer disclosure boundary.
     */
    private static MarkedSection findMarkedSection(String markdown) {
        int markerStart = markdown.indexOf(TEST_CASES_START);
        if (markerStart < 0) {
            return null;
        }
        int contentStart = markerStart + TEST_CASES_START.length();
        int markerEnd = markdown.indexOf(TEST_CASES_END, contentStart);
        if (markerEnd < 0) {
            return null;
        }

        String markedContent = markdown.substring(contentStart, markerEnd);
        Matcher testHeading = LEGACY_SECTION_PATTERN.matcher(markedContent);
        if (!testHeading.find()) {
            return new MarkedSection(
                    markerStart,
                    contentStart,
                    markerEnd,
                    contentStart,
                    markerEnd,
                    "### 3. Test Scenarios");
        }

        int testContentStart = contentStart + testHeading.start();
        int testHeadingEnd = contentStart + testHeading.end();
        int testContentEnd = markerEnd;
        int headingLevel = testHeading.group(1).length();

        Matcher followingHeadings = HEADING_PATTERN.matcher(markdown);
        followingHeadings.region(testHeadingEnd, markerEnd);
        while (followingHeadings.find()) {
            int followingLevel = followingHeadings.group(1).length();
            String followingTitle = followingHeadings.group(2).trim();
            if (followingLevel < headingLevel
                    || (followingLevel == headingLevel
                    && isLaterDocumentSection(followingTitle))) {
                testContentEnd = followingHeadings.start();
                break;
            }
        }

        return new MarkedSection(
                markerStart,
                contentStart,
                markerEnd,
                testContentStart,
                testContentEnd,
                testHeading.group().trim());
    }

    private static boolean isLaterDocumentSection(String headingTitle) {
        return NUMBERED_SECTION_HEADING.matcher(headingTitle).matches()
                || KNOWN_LATER_SECTION_HEADING.matcher(headingTitle).matches();
    }

    private static Section findEnvironmentSection(String markdown) {
        Matcher headings = HEADING_PATTERN.matcher(markdown);
        while (headings.find()) {
            String title = headings.group(2).trim();
            if (!ENVIRONMENT_SECTION_HEADING.matcher(title).matches()) {
                continue;
            }

            int headingLevel = headings.group(1).length();
            int sectionStart = headings.start();
            int sectionEnd = markdown.length();
            Matcher followingHeadings = HEADING_PATTERN.matcher(markdown);
            followingHeadings.region(headings.end(), markdown.length());
            while (followingHeadings.find()) {
                if (followingHeadings.group(1).length() <= headingLevel) {
                    sectionEnd = followingHeadings.start();
                    break;
                }
            }
            return new Section(
                    sectionStart,
                    sectionEnd,
                    markdown.substring(sectionStart, sectionEnd).trim());
        }
        return null;
    }

    private static String stripFirstHeading(String section) {
        return HEADING_PATTERN.matcher(section).replaceFirst("").trim();
    }

    private static String normalizeDocumentPart(String value) {
        return value == null
                ? ""
                : value.trim().replaceAll("\\n{3,}", "\n\n");
    }

    private static String normalizeNullablePart(String value) {
        String normalized = normalizeDocumentPart(value);
        return normalized.isBlank() ? null : normalized;
    }

    private static List<QaDocTestCase> parseTestCases(String section) {
        List<QaDocTestCase> structured = parseStructuredTestCases(section);
        if (!structured.isEmpty()) {
            return structured;
        }

        String fallback = stripSectionHeading(section);
        if (fallback.isBlank()) {
            return List.of();
        }
        return List.of(new QaDocTestCase("Test scenarios", null, null, fallback));
    }

    private static List<QaDocTestCase> parseStructuredTestCases(String section) {
        Matcher matcher = SCENARIO_PATTERN.matcher(section);
        List<ScenarioMatch> matches = new ArrayList<>();
        while (matcher.find()) {
            matches.add(new ScenarioMatch(
                    matcher.start(),
                    matcher.end(),
                    matcher.group(1).trim(),
                    matcher.group(2).toUpperCase(Locale.ROOT)
            ));
        }

        if (matches.isEmpty()) {
            return List.of();
        }

        List<QaDocTestCase> testCases = new ArrayList<>();
        for (int index = 0; index < matches.size(); index++) {
            ScenarioMatch current = matches.get(index);
            int descriptionEnd = index + 1 < matches.size()
                    ? matches.get(index + 1).start()
                    : section.length();

            Matcher interveningHeading = HEADING_PATTERN.matcher(section);
            interveningHeading.region(current.end(), descriptionEnd);
            if (interveningHeading.find()) {
                descriptionEnd = interveningHeading.start();
            }

            String description = section.substring(current.end(), descriptionEnd).trim();
            testCases.add(new QaDocTestCase(
                    current.title(),
                    current.priority(),
                    findFunctionalArea(section, current.start()),
                    description
            ));
        }
        return List.copyOf(testCases);
    }

    private static String findFunctionalArea(String section, int beforePosition) {
        Matcher headings = HEADING_PATTERN.matcher(section);
        String latest = null;
        while (headings.find() && headings.start() < beforePosition) {
            String candidate = headings.group(2).trim();
            if (!candidate.toLowerCase(Locale.ROOT).contains("test scenarios")) {
                latest = candidate;
            }
        }
        return latest;
    }

    private static String stripSectionHeading(String section) {
        return LEGACY_SECTION_PATTERN.matcher(section).replaceFirst("").trim();
    }

    private record Section(int start, int end, String content) {
    }

    private record MarkedSection(
            int markerStart,
            int contentStart,
            int markerEnd,
            int testContentStart,
            int testContentEnd,
            String heading
    ) {
    }

    private record ScenarioMatch(int start, int end, String title, String priority) {
    }
}

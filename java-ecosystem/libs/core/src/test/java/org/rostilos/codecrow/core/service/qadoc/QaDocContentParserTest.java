package org.rostilos.codecrow.core.service.qadoc;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class QaDocContentParserTest {

    @Test
    void separatesMarkedTestCasesFromOverviewAndPreservesStructuredDetails() {
        String markdown = """
                # QA Guide

                ## What Changed
                A user-visible flow changed.

                <!-- codecrow-test-cases:start -->
                ## 3. Test Scenarios by Area

                ### Checkout
                **Complete checkout** (HIGH)
                - **Preconditions:** A product is in the cart
                - **Steps:**
                  1. Submit the order
                - **Expected Result:** The confirmation is shown

                **Reject an empty address** (MEDIUM)
                - **Expected Result:** A validation message is shown
                <!-- codecrow-test-cases:end -->

                ## Regression Risks
                Verify saved carts.
                """;

        QaDocContent result = QaDocContentParser.parse(markdown);

        assertThat(result.overviewMarkdown())
                .contains("What Changed", "Regression Risks")
                .doesNotContain("Complete checkout", "codecrow-test-cases");
        assertThat(result.environmentMarkdown()).isNull();
        assertThat(result.testCases()).hasSize(2);
        assertThat(result.testCases().get(0))
                .extracting(QaDocTestCase::title, QaDocTestCase::priority, QaDocTestCase::functionalArea)
                .containsExactly("Complete checkout", "HIGH", "Checkout");
        assertThat(result.testCases().get(0).descriptionMarkdown())
                .contains("Preconditions", "Submit the order", "Expected Result");
    }

    @Test
    void extractsLegacyEnglishTestScenarioSections() {
        QaDocContent result = QaDocContentParser.parse("""
                ### 1. Change Summary
                Summary
                ### 3. Test Scenarios
                **Open settings** (LOW)
                - **Expected Result:** Settings appear
                ### 4. Edge Cases
                Try an empty state.
                """);

        assertThat(result.testCases()).singleElement()
                .extracting(QaDocTestCase::title)
                .isEqualTo("Open settings");
        assertThat(result.overviewMarkdown()).doesNotContain("Open settings");
    }

    @Test
    void publicParsingRequiresMarkersAndAStructuredScenario() {
        assertThat(QaDocContentParser.parseMarkedTestCases("""
                <!-- codecrow-test-cases:start -->
                Secret overview text without a scenario
                <!-- codecrow-test-cases:end -->
                """)).isEmpty();
        assertThat(QaDocContentParser.parseMarkedTestCases("""
                **Unmarked scenario** (HIGH)
                - **Expected Result:** It works
                """)).isEmpty();
    }

    @Test
    void replacesOnlyTheMarkedTestCaseBodyAndKeepsTheNumberedSection() {
        String result = QaDocContentParser.replaceShareableSections("""
                ### 1. Change Summary
                Checkout now supports gift cards.

                ### 2. Scope
                Web checkout only.

                <!-- codecrow-test-cases:start -->
                ### 3. Test Scenarios

                **Pay with a gift card** (HIGH)
                - **Steps:** Enter a valid gift card.
                - **Expected Result:** The balance is applied.
                <!-- codecrow-test-cases:end -->

                ### 4. Edge Cases
                Test an expired gift card.
                """,
                "https://codecrow.cloud/share#token=ccs_public-token&tab=test-cases",
                "https://codecrow.cloud/share#token=ccs_public-token&tab=environment");

        assertThat(result)
                .contains("### 1. Change Summary", "Checkout now supports gift cards")
                .contains("### 2. Scope", "Web checkout only")
                .contains("### 3. Test Scenarios\n\nhttps://codecrow.cloud/share#token=ccs_public-token")
                .contains("### 4. Edge Cases", "Test an expired gift card")
                .doesNotContain("Pay with a gift card", "The balance is applied");
    }

    @Test
    void preservesLaterPeerSectionsWhenTheModelPlacesTheEndMarkerTooLate() {
        String markdown = """
                ### 1. Change Summary
                Checkout changed.

                <!-- codecrow-test-cases:start -->
                ### 3. Test Scenarios
                ### Checkout
                **Pay with a gift card** (HIGH)
                - **Expected Result:** The balance is applied.

                ### 4. Edge Cases and Negative Testing
                - Try an expired gift card.

                ### 5. Regression Risks
                - Existing card payments.

                ### 6. Environment and Setup Notes
                - Use the QA payment environment.
                <!-- codecrow-test-cases:end -->
                """;

        QaDocContent parsed = QaDocContentParser.parse(markdown);
        String comment = QaDocContentParser.replaceShareableSections(
                markdown,
                "https://codecrow.cloud/share#token=ccs_public-token&tab=test-cases",
                "https://codecrow.cloud/share#token=ccs_public-token&tab=environment");

        assertThat(parsed.testCases()).singleElement()
                .extracting(QaDocTestCase::title, QaDocTestCase::functionalArea)
                .containsExactly("Pay with a gift card", "Checkout");
        assertThat(parsed.overviewMarkdown())
                .contains(
                        "### 4. Edge Cases and Negative Testing",
                        "### 5. Regression Risks")
                .doesNotContain(
                        "Pay with a gift card",
                        "codecrow-test-cases",
                        "### 6. Environment and Setup Notes",
                        "Use the QA payment environment");
        assertThat(parsed.environmentMarkdown())
                .isEqualTo("- Use the QA payment environment.");
        assertThat(comment)
                .contains("### 3. Test Scenarios\n\nhttps://codecrow.cloud/share#token=ccs_public-token&tab=test-cases")
                .contains(
                        "### 4. Edge Cases and Negative Testing",
                        "Try an expired gift card",
                        "### 5. Regression Risks",
                        "Existing card payments",
                        "### 6. Environment and Setup Notes",
                        "https://codecrow.cloud/share#token=ccs_public-token&tab=environment")
                .doesNotContain(
                        "Pay with a gift card",
                        "The balance is applied",
                        "Use the QA payment environment")
                .contains("tab=test-cases\n<!-- codecrow-test-cases:end -->\n\n### 4.");
    }

    @Test
    void replacesEnvironmentBodyAndPreservesTheGeneratedFooter() {
        String markdown = """
                ### 1. Change Summary
                Checkout changed.

                <!-- codecrow-test-cases:start -->
                ### 3. Test Scenarios
                **Pay successfully** (HIGH)
                - **Expected Result:** Payment succeeds.
                <!-- codecrow-test-cases:end -->

                ### 4. Edge Cases
                Try an expired card.

                ### 5. Regression Risks
                Verify saved cards.

                ### 6. Environment and Setup Notes
                Use the QA payment environment.

                ---
                *🐦 Generated by [CodeCrow](https://codecrow.app) QA Auto-Documentation*
                <!-- codecrow-qa-autodoc:prs=17 -->
                """;
        QaDocContent parsed = QaDocContentParser.parse(markdown);
        String comment = QaDocContentParser.replaceShareableSections(
                markdown,
                "https://codecrow.cloud/share#token=ccs_public-token&tab=test-cases",
                "https://codecrow.cloud/share#token=ccs_public-token&tab=environment");

        assertThat(parsed.overviewMarkdown())
                .contains("Change Summary", "Edge Cases", "Regression Risks")
                .doesNotContain("Generated by", "codecrow-qa-autodoc");
        assertThat(parsed.environmentMarkdown()).isEqualTo("Use the QA payment environment.");
        assertThat(comment)
                .contains("### 4. Edge Cases", "Try an expired card")
                .contains("### 5. Regression Risks", "Verify saved cards")
                .contains("### 6. Environment and Setup Notes\n\n"
                        + "https://codecrow.cloud/share#token=ccs_public-token&tab=environment")
                .contains("Generated by [CodeCrow]", "<!-- codecrow-qa-autodoc:prs=17 -->")
                .doesNotContain("Use the QA payment environment.");
    }

    @Test
    void separatesSetupAndEnvironmentNotesFromAnUnmarkedDocument() {
        QaDocContent parsed = QaDocContentParser.parse("""
                # QA Testing Guide — Checkout

                ## 1. What Changed
                Saved cards can now be selected at checkout.

                ## 6. Setup and Environment Notes
                - Enable the saved-card feature flag.
                - Use a customer with an existing payment method.
                """);

        assertThat(parsed.overviewMarkdown())
                .contains("What Changed", "Saved cards")
                .doesNotContain("Setup and Environment Notes", "feature flag");
        assertThat(parsed.environmentMarkdown())
                .contains("Enable the saved-card feature flag", "existing payment method")
                .doesNotContain("## 6.");
    }

    @Test
    void preservesSectionsAfterEnvironmentNotesInTheOverview() {
        QaDocContent parsed = QaDocContentParser.parse("""
                ## Overview
                Summary.

                ## Environment and Setup Notes
                Use staging.

                ## Appendix
                Contact the QA lead.
                """);

        assertThat(parsed.environmentMarkdown()).isEqualTo("Use staging.");
        assertThat(parsed.overviewMarkdown()).contains("Overview", "Appendix", "Contact the QA lead");
    }
}

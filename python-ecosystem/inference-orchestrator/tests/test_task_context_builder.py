"""
Unit tests for utils.task_context_builder —
extract_acceptance_criteria, build_task_context, build_task_context_for_prompt.
"""
import pytest
from utils.task_context_builder import (
    extract_acceptance_criteria,
    build_task_context,
    build_task_context_for_prompt,
)


# ── extract_acceptance_criteria ──────────────────────────────────

class TestExtractAcceptanceCriteria:

    def test_h3_header(self):
        desc = "Some intro.\nh3. Acceptance Criteria\n- User can login\n- User can logout"
        ac = extract_acceptance_criteria(desc)
        assert ac is not None
        assert "User can login" in ac

    def test_markdown_header(self):
        desc = "## Acceptance Criteria\n1. System validates input\n2. Error displayed"
        ac = extract_acceptance_criteria(desc)
        assert "validates input" in ac

    def test_bold_header(self):
        desc = "**Acceptance Criteria**\n- Works in dark mode\n- Responsive"
        ac = extract_acceptance_criteria(desc)
        assert "Works in dark mode" in ac

    def test_ac_abbreviation(self):
        desc = "## AC:\n- Item 1\n- Item 2"
        ac = extract_acceptance_criteria(desc)
        assert "Item 1" in ac

    def test_definition_of_done(self):
        desc = "## Definition of Done\n- All tests pass\n- Code reviewed"
        ac = extract_acceptance_criteria(desc)
        assert "All tests pass" in ac

    def test_stops_at_next_section(self):
        desc = (
            "## Acceptance Criteria\n- Criterion 1\n"
            "## Technical Notes\nSome notes"
        )
        ac = extract_acceptance_criteria(desc)
        assert ac is not None
        assert "Criterion 1" in ac
        assert "Technical Notes" not in ac

    def test_none_for_empty(self):
        assert extract_acceptance_criteria(None) is None
        assert extract_acceptance_criteria("") is None

    def test_none_when_no_ac_header(self):
        assert extract_acceptance_criteria("Just a regular description.") is None

    def test_none_when_ac_too_short(self):
        desc = "## Acceptance Criteria\nOK"
        ac = extract_acceptance_criteria(desc)
        assert ac is None


# ── build_task_context ───────────────────────────────────────────

class TestBuildTaskContext:

    def test_none_input(self):
        assert build_task_context(None) == ""

    def test_empty_dict(self):
        assert build_task_context({}) == ""

    def test_with_key_and_summary(self):
        ctx = {"task_key": "PROJ-123", "task_summary": "Fix login bug"}
        result = build_task_context(ctx)
        assert "PROJ-123" in result
        assert "Fix login bug" in result

    def test_metadata_chips(self):
        ctx = {
            "task_key": "X-1",
            "task_summary": "S",
            "task_type": "Bug",
            "status": "In Progress",
            "priority": "High",
            "assignee": "Dev A",
            "reporter": "PM B",
            "web_url": "https://jira.example/browse/X-1",
        }
        result = build_task_context(ctx)
        assert "Bug" in result
        assert "In Progress" in result
        assert "High" in result
        assert "Dev A" in result
        assert "PM B" in result
        assert "https://jira.example/browse/X-1" in result

    def test_accepts_camel_case_keys(self):
        ctx = {
            "taskKey": "PROJ-9",
            "taskSummary": "Add account export",
            "taskType": "Story",
            "webUrl": "https://jira.example/browse/PROJ-9",
        }
        result = build_task_context(ctx)
        assert "PROJ-9" in result
        assert "Add account export" in result
        assert "Story" in result
        assert "https://jira.example/browse/PROJ-9" in result

    def test_includes_acceptance_criteria(self):
        ctx = {
            "task_key": "X-1",
            "task_summary": "S",
            "description": "## Acceptance Criteria\n- Criterion one\n- Criterion two",
        }
        result = build_task_context(ctx, include_acceptance_criteria=True)
        assert "Acceptance Criteria" in result
        assert "Criterion one" in result

    def test_excludes_acceptance_criteria(self):
        ctx = {
            "task_key": "X-1",
            "task_summary": "S",
            "description": "## Acceptance Criteria\n- Criterion one",
        }
        result = build_task_context(ctx, include_acceptance_criteria=False)
        # When AC is excluded, no dedicated AC section is added, but the raw
        # description (which may contain AC text) is still shown.
        assert "#### Acceptance Criteria" not in result

    def test_description_truncation(self):
        ctx = {
            "task_key": "X-1",
            "task_summary": "S",
            "description": "A" * 3000,
        }
        result = build_task_context(ctx, max_description_length=100)
        assert "…" in result

    def test_removes_ac_from_description(self):
        """AC should not appear twice (once in AC section, once in description)."""
        ctx = {
            "task_key": "X-1",
            "task_summary": "S",
            "description": "Intro text.\n## Acceptance Criteria\n- Must work",
        }
        result = build_task_context(ctx)
        # "Must work" should appear only in AC section
        assert result.count("Must work") == 1

    def test_preserves_sections_after_acceptance_criteria(self):
        ctx = {
            "task_key": "X-1",
            "task_summary": "S",
            "description": (
                "Intro text.\n"
                "## Acceptance Criteria\n"
                "- Must work for admins\n"
                "## Technical Notes\n"
                "Use cached totals from the summary table."
            ),
        }
        result = build_task_context(ctx)
        assert result.count("Must work for admins") == 1
        assert "Intro text." in result
        assert "Technical Notes" in result
        assert "Use cached totals from the summary table." in result

    def test_preserves_sections_after_ac_abbreviation(self):
        ctx = {
            "task_key": "X-1",
            "task_summary": "S",
            "description": (
                "Intro text.\n"
                "## AC:\n"
                "- Export active customers\n"
                "## Implementation Notes\n"
                "Reuse the existing CSV writer."
            ),
        }
        result = build_task_context(ctx)
        assert result.count("Export active customers") == 1
        assert "Intro text." in result
        assert "Implementation Notes" in result
        assert "Reuse the existing CSV writer." in result


# ── build_task_context_for_prompt ────────────────────────────────

class TestBuildTaskContextForPrompt:

    def test_no_context(self):
        result = build_task_context_for_prompt(None)
        assert "No task context" in result

    def test_with_context(self):
        ctx = {"task_key": "X-1", "task_summary": "Fix it"}
        result = build_task_context_for_prompt(ctx)
        assert "X-1" in result

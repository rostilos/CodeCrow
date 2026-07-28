from tools.review_quality.prompt_gate_profile import (
    FIXED_PROMPT_GATE_ENV,
    apply_fixed_prompt_gate_profile,
    stable_prompt_digest,
)


def test_fixed_prompt_gate_profile_overrides_process_specific_values():
    environment = {
        "REVIEW_STAGE1_MAX_FILES_PER_BATCH": "3",
        "UNRELATED": "retained",
    }

    apply_fixed_prompt_gate_profile(environment)

    assert environment["UNRELATED"] == "retained"
    assert environment["REVIEW_STAGE1_MAX_FILES_PER_BATCH"] == "15"
    assert {
        key: environment[key]
        for key in FIXED_PROMPT_GATE_ENV
    } == FIXED_PROMPT_GATE_ENV


def test_prompt_digest_ignores_parallel_sequence_but_not_content():
    first = {
        "sequence": 2,
        "stage": "stage_1",
        "renderedPrompt": "first batch",
    }
    second = {
        "sequence": 3,
        "stage": "stage_1",
        "renderedPrompt": "second batch",
    }

    assert stable_prompt_digest((first, second)) == stable_prompt_digest((
        {**second, "sequence": 2},
        {**first, "sequence": 3},
    ))
    assert stable_prompt_digest((first, second)) != stable_prompt_digest((
        first,
        {**second, "renderedPrompt": "changed"},
    ))

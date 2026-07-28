from __future__ import annotations

import asyncio

import pytest

from service.review.prompt_diagnostics import (
    capture_prompt_diagnostics,
    record_prompt_diagnostic,
)


@pytest.mark.asyncio
async def test_prompt_diagnostics_are_isolated_between_concurrent_reviews():
    async def capture(review: str):
        records = []
        with capture_prompt_diagnostics(records.append):
            await asyncio.sleep(0)
            record_prompt_diagnostic({
                "stage": "stage_1",
                "review": review,
            })
        record_prompt_diagnostic({
            "stage": "stage_1",
            "review": "outside",
        })
        return records

    left, right = await asyncio.gather(
        capture("left"),
        capture("right"),
    )

    assert left == [{"stage": "stage_1", "review": "left"}]
    assert right == [{"stage": "stage_1", "review": "right"}]

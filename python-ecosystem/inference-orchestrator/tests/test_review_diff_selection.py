"""Path-based changed-source retrieval avoids whole-PR hunk-ID inventories."""
from types import SimpleNamespace

import pytest

from service.review.verification_tools import VerificationTools


@pytest.mark.asyncio
async def test_diff_selects_complete_hunks_by_union_of_exact_paths_and_ids():
    parts = [SimpleNamespace(id=f"hunk-{number}", path=path, side="proposed",
                             anchors={number: f"change{number}"}, diff=f"full diff {number}\n" * 200)
             for number, path in enumerate(("a.py", "b.py", "a.py", "a.py-extra"), 1)]
    tools = VerificationTools(rag_client=None, binding={}, parts=parts)
    result = await tools.call("getReviewDiff", {"partIds": ["hunk-2", "hunk-2", "unknown"],
                                               "paths": ["a.py", "missing.py"]})
    assert [part["id"] for part in result["parts"]] == ["hunk-1", "hunk-2", "hunk-3"]
    assert [part["diff"] for part in result["parts"]] == [part.diff for part in parts[:3]]
    assert result["missingPartIds"] == ["unknown"]
    assert result["missingPaths"] == ["missing.py"]
    empty = await tools.call("getReviewDiff", {})
    assert empty["parts"] == [] and empty["status"] == "unavailable"
    assert "paths or partIds" in empty["diagnostic"]

import json
from pathlib import Path

from codecrow_plugins.plugin_glob import plugin_glob_matches


FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "contracts"
    / "fixtures"
    / "plugin-globs.json"
)


def test_plugin_globs_match_the_shared_anchored_projection():
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert [
        plugin_glob_matches(case["glob"], case["path"])
        for case in cases
    ] == [case["matches"] for case in cases]

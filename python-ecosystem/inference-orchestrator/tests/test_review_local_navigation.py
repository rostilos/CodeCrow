"""Snapshot navigation regressions found in captured verifier conversations."""
import json
from pathlib import Path

import pytest

from service.review.local_source import LocalReviewSource
from service.review.verification_tools import VerificationTools


@pytest.fixture
def snapshot(tmp_path):
    target, overlay = tmp_path / "target", tmp_path / "overlay"
    source = {
        "app/templates/components/home-logo.hbs": '<a class="title">Home</a>\n',
        "app/styles/header.scss": ".row {\n  color: red;\n}\n.title { display: block; }\n",
        "app/deleted.hbs": "deleted template\n",
        "root.hbs": "root template\n",
    }
    for path, text in source.items():
        file = target / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)
    changed = "app/styles/header.scss"
    added = "app/templates/new-title.hbs"
    for path, text in {changed: ".row {\n  color: blue;\n}\n.title { display: flex; }\n", added: "title\n"}.items():
        file = overlay / "files" / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)
    (overlay / "manifest.json").write_text(json.dumps({
        "changedFiles": [changed, added], "deletedFiles": ["app/deleted.hbs"],
    }))
    return {"target_repo_path": str(target), "review_overlay_path": str(overlay)}


def test_find_files_locates_unreferenced_template_with_glob_and_snapshot_semantics(snapshot):
    source = LocalReviewSource(snapshot)
    assert source.find_files("*home-logo*")["paths"] == ["app/templates/components/home-logo.hbs"]
    assert source.find_files("app/**/home-logo.hbs")["paths"] == ["app/templates/components/home-logo.hbs"]
    assert source.find_files("app/*/home-logo.hbs")["paths"] == []
    proposed = source.find_files("**/*.hbs")
    assert proposed["status"] == "ready" and proposed["complete"]
    assert proposed["paths"] == ["app/templates/components/home-logo.hbs", "app/templates/new-title.hbs", "root.hbs"]
    target = source.find_files("*.hbs", side="target")
    assert target["paths"] == ["app/deleted.hbs", "app/templates/components/home-logo.hbs", "root.hbs"]
    assert source.find_files("*.hbs", paths=["app/templates/components"])["paths"] == ["app/templates/components/home-logo.hbs"]
    assert source.find_files("*.hbs", paths=["not-present"])["complete"] is False


def test_find_files_does_not_read_source_bodies(snapshot, monkeypatch):
    source = LocalReviewSource(snapshot)
    monkeypatch.setattr(source, "read", lambda *args, **kwargs: pytest.fail("filename lookup read source"))
    assert source.find_files("*.hbs")["status"] == "ready"


def test_find_files_reports_missing_overlay_instead_of_old_target(snapshot):
    Path(snapshot["review_overlay_path"], "files/app/styles/header.scss").unlink()
    result = LocalReviewSource(snapshot).find_files("*.scss")
    assert result["status"] == "partial" and result["complete"] is False
    assert result["paths"] == []
    assert result["unavailablePaths"] == ["app/styles/header.scss"]


def test_local_navigation_never_follows_other_tenant_links_or_git_metadata(snapshot, tmp_path):
    target = Path(snapshot["target_repo_path"])
    external = tmp_path / "other-tenant"
    external.mkdir()
    (external / "private.hbs").write_text("secret\n")
    (target / "linked").symlink_to(external, target_is_directory=True)
    (target / "file.hbs").symlink_to(external / "private.hbs")
    (target / ".git").mkdir()
    (target / ".git" / "private.hbs").write_text("metadata\n")
    overlay_file = Path(snapshot["review_overlay_path"], "files/app/templates/new-title.hbs")
    overlay_file.unlink()
    overlay_file.symlink_to(external / "private.hbs")
    source = LocalReviewSource(snapshot)
    result = source.find_files("*.hbs")
    assert result["status"] == "partial"
    assert result["paths"] == ["app/templates/components/home-logo.hbs", "root.hbs"]
    assert result["unavailablePaths"] == ["app/templates/new-title.hbs"]
    assert source.find_files("*.hbs", paths=["linked"])["status"] == "partial"
    for pattern in ("../other-tenant/*.hbs", str(external / "*.hbs"), ".git/*"):
        assert source.find_files(pattern)["status"] == "unavailable"
    assert source.grep("secret", mode="regex")["results"] == []


def test_glob_results_are_not_capped(snapshot):
    target = Path(snapshot["target_repo_path"])
    for index in range(301):
        (target / f"extra-{index}.hbs").write_text("template\n")
    result = LocalReviewSource(snapshot).find_files("extra-*.hbs")
    assert result["complete"] and len(result["paths"]) == 301


def test_missing_target_does_not_claim_overlay_directory_search_is_complete(snapshot):
    source = LocalReviewSource({"review_overlay_path": snapshot["review_overlay_path"]})
    assert source.find_files("*.hbs", paths=["app/templates"])["status"] == "partial"
    exact = source.find_files("*.hbs", paths=["app/templates/new-title.hbs"])
    assert exact["complete"] and exact["paths"] == ["app/templates/new-title.hbs"]


def test_regex_mode_resolves_real_trace_anchors_and_alternation_without_changing_literal_mode(snapshot):
    source = LocalReviewSource(snapshot)
    query = r"^\.row|^\.title"
    assert source.grep(query, mode="literal")["results"] == []
    result = source.grep(query, mode="regex", paths=["app/styles"])
    assert result["complete"] and result["mode"] == "regex"
    assert result["results"] == [{"path": "app/styles/header.scss", "matches": [
        {"line": 1, "text": ".row {"}, {"line": 4, "text": ".title { display: flex; }"},
    ]}]
    assert source.grep("COLOR: BLUE", mode="regex", case_sensitive=False)["results"][0]["matches"] == [
        {"line": 2, "text": "  color: blue;"},
    ]
    assert source.grep("red", mode="regex", side="target")["results"]
    assert not source.grep("red", mode="regex")["results"]


def test_regex_preserves_full_multiline_matches_and_all_long_lines(snapshot):
    path = Path(snapshot["target_repo_path"], "long.txt")
    long_line = "needle" + "x" * 50000
    path.write_text("\n".join([long_line] * 130) + "\nstart\nend\n")
    source = LocalReviewSource(snapshot)
    literal = source.grep("needle", paths=["long.txt"])
    assert len(literal["results"][0]["matches"]) == 130
    assert all(match["text"] == long_line for match in literal["results"][0]["matches"])
    result = source.grep("start\\nend", mode="regex", paths=["long.txt"])
    assert result["results"][0]["matches"] == [{"line": 131, "text": "start"}, {"line": 132, "text": "end"}]


def test_invalid_regex_is_actionable_not_a_complete_negative_result(snapshot):
    result = LocalReviewSource(snapshot).grep("[", mode="regex")
    assert result["status"] == "unavailable" and result["complete"] is False
    assert "Invalid regular expression" in result["diagnostic"]
    assert "mode=literal" in result["diagnostic"]
    assert LocalReviewSource(snapshot).grep("[", mode="literal")["status"] == "ready"


def test_regex_timeout_interrupts_matching_and_preserves_prior_results(snapshot, monkeypatch):
    from service.review import local_source
    target = Path(snapshot["target_repo_path"])
    (target / "000-good.txt").write_text("needle\n")
    (target / "001-pathological.txt").write_text("a" * 50000 + "!\n")
    monkeypatch.setattr(local_source, "_REGEX_MATCH_TIMEOUT_SECONDS", 0.01)
    result = LocalReviewSource(snapshot).grep(r"needle|(?:a+)+$", mode="regex")
    assert result["status"] == "partial" and result["complete"] is False
    assert result["results"] == [{"path": "000-good.txt", "matches": [{"line": 1, "text": "needle"}]}]
    assert "001-pathological.txt" in result["unavailablePaths"]
    assert "timeout" in result["diagnostic"]


@pytest.mark.asyncio
async def test_mcp_navigation_exposes_required_search_mode_and_host_bound_path_lookup(snapshot):
    tools = VerificationTools(rag_client=None, binding=snapshot, parts=[])
    schemas = {tool["name"]: tool["inputSchema"] for tool in await tools.schemas()}
    assert schemas["grepReviewCode"]["required"] == ["query", "mode"]
    assert schemas["grepReviewCode"]["properties"]["mode"]["enum"] == ["literal", "regex"]
    assert schemas["findReviewFiles"]["required"] == ["pattern"]
    assert "root" not in schemas["findReviewFiles"]["properties"]
    result = await tools.call("findReviewFiles", {"pattern": "*home-logo*"})
    assert result["paths"] == ["app/templates/components/home-logo.hbs"]
    regex_result = await tools.call("grepReviewCode", {"query": r"^\.row", "mode": "regex"})
    assert regex_result["results"][0]["matches"] == [{"line": 1, "text": ".row {"}]
    missing_mode = await tools.call("grepReviewCode", {"query": r"^\.row"})
    assert missing_mode["status"] == "unavailable"

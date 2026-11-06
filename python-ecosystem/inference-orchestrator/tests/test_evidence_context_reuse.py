import json
from types import SimpleNamespace

from service.agent.evidence_acquisition import compact_evidence_messages


class Message(SimpleNamespace):
    def model_copy(self, *, update):
        return Message(**{**vars(self), **update})


SOURCE = "def validate(owner, record):\n    return record.workspace_id == owner.workspace_id\n" * 12


def tool(value, name="getMinimalReviewContext"):
    return Message(type="tool", name=name, content=json.dumps(value, indent=2), artifact=value)


def resolve(messages, reference):
    text = messages[reference["messageIndex"]].content
    if reference["jsonPointer"]:
        text = json.loads(text)
        for key in reference["jsonPointer"].split("/")[1:]:
            text = text[int(key)] if isinstance(text, list) else text[key.replace("~1", "/").replace("~0", "~")]
    start = reference["characterOffset"]
    return text[start:start + reference["characterLength"]]


def test_source_reference_preserves_graph_snapshot_and_exact_body():
    payload = {"snapshot": {"revision": "head"}, "edges": [{"source": "a", "target": "b"}],
               "sourceWindows": [{"path": "auth.py", "startLine": 1, "content": SOURCE, "evidenceId": "one"}]}
    original = [Message(type="human", content="Current source:\n" + SOURCE), tool(payload)]
    result, stats = compact_evidence_messages(original)
    compact = json.loads(result[1].content)
    reference = compact["sourceWindows"][0].pop("contentReference")
    compact["sourceWindows"][0]["content"] = resolve(result, reference)
    assert compact == payload
    assert result[1].artifact is payload
    assert json.loads(original[1].content) == payload
    assert stats["sourceBodiesReused"] == 1
    assert stats["visibleToolBytes"] < stats["originalToolBytes"]


def test_repeated_tool_source_points_to_first_body_without_reference_chains():
    original = [tool({"fileContent": SOURCE}, "getReviewFileContent"),
                tool({"unit": {"path": "other.py", "revision": "other-revision", "content": SOURCE}}, "getStructuralUnit"),
                tool({"sourceWindows": [{"content": SOURCE}]})]
    result, stats = compact_evidence_messages(original)
    unit = json.loads(result[1].content)["unit"]
    last = json.loads(result[2].content)["sourceWindows"][0]
    assert resolve(result, unit["contentReference"]) == SOURCE
    assert resolve(result, last["contentReference"]) == SOURCE
    assert unit["path"] == "other.py" and unit["revision"] == "other-revision"
    assert unit["contentReference"]["messageIndex"] == last["contentReference"]["messageIndex"] == 0
    assert stats["sourceBodiesReused"] == 2
    again, _ = compact_evidence_messages(original)
    assert [m.content for m in result] == [m.content for m in again]


def test_history_truncation_and_new_review_restore_body():
    original = [Message(type="human", content=SOURCE), tool({"fileContent": SOURCE}, "getReviewFileContent")]
    result, _ = compact_evidence_messages(original)
    assert "fileContentReference" in json.loads(result[-1].content)
    fresh, _ = compact_evidence_messages(original[1:])
    assert json.loads(fresh[0].content)["fileContent"] == SOURCE


def test_different_source_and_non_source_fields_remain_complete():
    payload = {"summary": SOURCE, "sourceWindows": [{"content": SOURCE + "# changed\n"}], "coverage": {"missing": True}}
    original = [Message(type="human", content=SOURCE), tool(payload)]
    result, stats = compact_evidence_messages(original)
    assert json.loads(result[-1].content) == payload
    assert stats["sourceBodiesReused"] == 0


def test_malformed_and_unsupported_tool_content_fails_open():
    original = [Message(type="tool", name="getMinimalReviewContext", content="{unfinished"),
                Message(type="tool", name="getMinimalReviewContext", content=[{"type": "text", "text": SOURCE}]),
                tool({"fileContent": SOURCE}, "unrelated_tool")]
    result, stats = compact_evidence_messages(original)
    assert result == original
    assert stats["sourceBodiesReused"] == 0

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
import pytest


TEST_SUPPORT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(TEST_SUPPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_SUPPORT_ROOT))

from codecrow_test_harness.ledger import ExternalCallLedger
from codecrow_test_harness.scenario import ScriptedScenario


SHARED_ROOT = REPOSITORY_ROOT / "tools" / "offline-harness"


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def test_python_consumes_canonical_cross_language_ledger_golden() -> None:
    schema = _load(SHARED_ROOT / "schema" / "external-call-ledger-v1.schema.json")
    golden = _load(SHARED_ROOT / "fixtures" / "golden" / "external-call-ledger-v1.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(golden)

    ledger = ExternalCallLedger()
    for call in golden["calls"]:  # type: ignore[index]
        call = dict(call)
        call.pop("sequence")
        ledger.record(**call)
    assert ledger.to_document() == golden
    ledger.assert_zero_live_calls()

    for unsafe_target in (
        "https://user:secret@example.com/private?token=value",
        "customer prompt payload",
        "-leading.invalid:443",
        "example.invalid:65536",
        "EXAMPLE.INVALID:443",
    ):
        mutated = deepcopy(golden)
        mutated["calls"][0]["target"] = unsafe_target  # type: ignore[index]
        with pytest.raises(ValidationError):
            Draft202012Validator(schema).validate(mutated)


def test_python_and_schema_consume_shared_target_redaction_corpus() -> None:
    schema = _load(SHARED_ROOT / "schema" / "external-call-ledger-v1.schema.json")
    corpus = _load(SHARED_ROOT / "fixtures" / "golden" / "target-redaction-v1.json")
    ledger = ExternalCallLedger()
    actual: list[str] = []
    for case in corpus["cases"]:  # type: ignore[index]
        call = ledger.record(
            boundary="network",
            operation="connect",
            outcome="blocked",
            phase="PRE_DNS",
            target=case["input"],
        )
        actual.append(call.target)
    assert actual == [case["output"] for case in corpus["cases"]]  # type: ignore[index]
    Draft202012Validator(schema).validate(ledger.to_document())


def test_python_consumes_canonical_cross_language_scenario_golden() -> None:
    schema = _load(SHARED_ROOT / "schema" / "scripted-scenario-v1.schema.json")
    golden = _load(SHARED_ROOT / "fixtures" / "golden" / "scripted-scenario-v1.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(golden)
    assert ScriptedScenario.from_document(golden).to_document() == golden  # type: ignore[arg-type]

    for mutated in (
        {**golden, "scenario_id": " padded"},  # type: ignore[arg-type]
        {**golden, "scenario_id": "line\nbreak"},  # type: ignore[arg-type]
        {**golden, "scenario_id": "nbsp\u00a0inside"},  # type: ignore[arg-type]
        {**golden, "scenario_id": "\ufeffbom"},  # type: ignore[arg-type]
        _with_padded_operation(golden),
        {**golden, "unknown": True},  # type: ignore[arg-type]
    ):
        with pytest.raises(ValidationError):
            Draft202012Validator(schema).validate(mutated)


def test_neutral_protocol_fixtures_are_versioned_and_contain_no_credentials() -> None:
    fixtures = sorted((SHARED_ROOT / "fixtures" / "protocol").glob("*.json"))
    assert [path.name for path in fixtures] == [
        "bitbucket-v1.json",
        "embedding-v1.json",
        "github-v1.json",
        "gitlab-v1.json",
        "jira-v1.json",
    ]
    for path in fixtures:
        text = path.read_text(encoding="utf-8")
        document = json.loads(text)
        assert document["schema_version"] == "1.0"
        lowered = text.lower()
        assert "authorization" not in lowered
        assert "api_key" not in lowered
        assert "password" not in lowered


def _with_padded_operation(golden: object) -> dict[str, object]:
    mutated = deepcopy(golden)
    mutated["steps"][0]["operation"] = " padded"  # type: ignore[index]
    return mutated  # type: ignore[return-value]

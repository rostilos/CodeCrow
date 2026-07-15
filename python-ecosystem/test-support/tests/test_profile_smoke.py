from __future__ import annotations

import sys
from pathlib import Path

import pytest


TEST_SUPPORT_ROOT = Path(__file__).resolve().parents[1]
if str(TEST_SUPPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_SUPPORT_ROOT))

from codecrow_test_harness.fakes import ScriptedBoundaryFake
from codecrow_test_harness.scenario import ScenarioStep, ScriptedScenario


def test_loaded_profile_records_a_scripted_call_in_its_process_ledger(
    request: pytest.FixtureRequest,
) -> None:
    if not request.config.pluginmanager.hasplugin("codecrow_test_harness.pytest_plugin"):
        pytest.skip("selected explicitly by the plugin-loaded offline profile smoke")

    ledger = request.getfixturevalue("external_call_ledger")
    fake = ScriptedBoundaryFake(
        boundary="telemetry",
        target="fake-telemetry:4318",
        scenario=ScriptedScenario(
            "offline-profile-smoke-v1",
            (ScenarioStep("telemetry.export", 1, "response", payload="accepted"),),
        ),
        ledger=ledger,
    )

    assert fake.call("telemetry.export").payload == "accepted"
    ledger.assert_zero_live_calls()
    assert ledger.simulated_call_count == 1

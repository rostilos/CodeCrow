from __future__ import annotations

from tools.review_quality.magento_prompt_gate import run_gate


def test_fixed_magento_prompt_gate_delivers_exact_context_without_provider_calls():
    report = run_gate()

    assert report["status"] == "passed"
    assert report["checks"] == {
        "providerCalls": True,
        "phpMagentoHyvaSelected": True,
        "deterministicRetrievalUsed": True,
        "exactFactsReturned": True,
        "expectedMagentoRelationshipsVisible": True,
        "publisherlessInboundHandlerVisible": True,
        "expectedGeneratedFactoryRelationshipsVisible": True,
        "expectedGeneratedProxyRelationshipsVisible": True,
        "expectedPhpCodeRelationshipsVisible": True,
        "exactPhpTargetReturnContractVisible": True,
        "exactPhpCallReturnChainVisible": True,
        "templateGlobalRelationshipVisible": True,
        "templateEventRelationshipVisible": True,
        "hyvaTemplateWebapiContextVisible": True,
        "hyvaAlpineComponentContextVisible": True,
        "hyvaAlpineEventContextVisible": True,
        "hyvaEvidenceContractVisible": True,
        "hyvaRuntimeVariableContextVisible": True,
        "pricePoolContextVisible": True,
        "phpEvidenceTargetsVisible": True,
        "noHiddenPluginEvidenceTargets": True,
        "promptInputTokenCeiling": True,
        "stage1InputTokenCeiling": True,
    }
    assert report["missingExpectedRelationships"] == []
    assert report["missingGeneratedProxyRelationships"] == []
    assert report["missingPhpCodeRelationships"] == []
    assert report["missingPhpEvidenceTargets"] == []
    assert report["providerCalls"] == 0
    assert report["prompt"]["qualitySignals"]["stage1"][
        "hiddenPluginEvidenceTargets"
    ] == 0
    assert report["prompt"]["qualitySignals"]["stage1"][
        "ragEvidenceEntries"
    ] > 0


def test_fixed_magento_prompt_gate_is_byte_stable():
    assert run_gate() == run_gate()

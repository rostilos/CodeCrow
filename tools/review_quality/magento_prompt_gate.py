#!/usr/bin/env python3
"""Audit Magento graph-to-prompt delivery without calling a model provider."""

from __future__ import annotations

import argparse
import asyncio
import difflib
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_CONTRACTS = PROJECT_ROOT / "analysis-plugins" / "contracts" / "python"
INFERENCE_SOURCE = (
    PROJECT_ROOT / "python-ecosystem" / "inference-orchestrator" / "src"
)
DEFAULT_CORPUS = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "magento_seeded_candidates.json"
)

# This audit must exercise one implementation tree end to end. Without an
# explicit root, prompt assembly can discover a plugin bundle baked into the
# execution image while repository analysis uses the mounted working tree.
os.environ["CODECROW_PLUGINS_ROOT"] = str(PROJECT_ROOT / "analysis-plugins")

for source_root in (
    str(INFERENCE_SOURCE),
    str(PLUGIN_CONTRACTS),
    str(PROJECT_ROOT),
):
    if source_root not in sys.path:
        sys.path.insert(0, source_root)

from tools.review_quality.prompt_gate_profile import (  # noqa: E402
    apply_fixed_prompt_gate_profile,
    stable_prompt_digest,
    stable_prompt_record_digests,
)

apply_fixed_prompt_gate_profile()

from codecrow_plugins import FileArtifact, PluginRuntime  # noqa: E402
from service.review.prompt_dry_run import capture_review_prompts  # noqa: E402
from utils.diff_processor import DiffProcessor  # noqa: E402
from tools.review_quality.magento_seeded_gate import (  # noqa: E402
    _build_runtime,
    _canonical_bytes,
    _fact_payload,
    _load_corpus,
    _request,
)


def _all_added_diff(artifacts: Mapping[str, str]) -> str:
    sections: list[str] = []
    for path in sorted(artifacts):
        content = artifacts[path]
        unified = "".join(difflib.unified_diff(
            [],
            content.splitlines(keepends=True),
            fromfile="/dev/null",
            tofile=f"b/{path}",
            lineterm="\n",
        ))
        sections.append((
            f"diff --git a/{path} b/{path}\n"
            "new file mode 100644\n"
            f"{unified}"
        ).rstrip("\n"))
    return "\n".join(sections) + ("\n" if sections else "")


def _facts_text(
    kind: str,
    source_path: str,
    payloads: Sequence[Mapping[str, Any]],
) -> str:
    lines = [
        "Deterministic repository architecture context",
        (
            "Plugin: hyva"
            if kind.startswith("hyva-")
            else (
                "Plugin: magento"
                if kind.startswith("magento-")
                else "Plugin: php"
            )
        ),
        f"Kind: {kind}",
        f"Source: {source_path}",
        "Facts:",
    ]
    for payload in payloads:
        attributes = payload.get("attributes")
        attribute_text = ""
        if isinstance(attributes, Mapping) and attributes:
            attribute_text = " {" + ", ".join(
                f"{key}={attributes[key]}" for key in sorted(attributes)
            ) + "}"
        lines.append(
            f"- [{payload['kind']}] {payload['source']} "
            f"{payload['relation']} {payload['target']} "
            f"({payload['path']}:{payload.get('line', 1)})"
            f"{attribute_text}"
        )
    return "\n".join(lines)


class FixtureGraphRagClient:
    """Exact-only RAG adapter over facts produced by the real plugin runtime."""

    def __init__(self, facts: Sequence[Any]):
        self._facts = tuple(sorted(facts))
        self.requests: list[tuple[str, ...]] = []
        self.returned_fact_count = 0

    async def get_deterministic_context(
        self,
        *,
        file_paths: Sequence[str],
        **_: Any,
    ) -> dict[str, Any]:
        requested = {
            str(path).lstrip("/").replace("\\", "/")
            for path in file_paths
            if path
        }
        self.requests.append(tuple(sorted(requested)))
        grouped: dict[
            tuple[str, str],
            list[tuple[dict[str, Any], set[str]]],
        ] = {}
        for fact in self._facts:
            payload = _fact_payload(fact)
            fact_paths = {
                str(payload["path"]).lstrip("/").replace("\\", "/"),
                *(
                    str(path).lstrip("/").replace("\\", "/")
                    for path in payload.get("related_paths", ())
                    if path
                ),
            }
            if not fact_paths.intersection(requested):
                continue
            grouped.setdefault(
                (str(payload["kind"]), str(payload["path"])),
                [],
            ).append((payload, fact_paths))

        chunks = []
        for (kind, source_path), records in sorted(grouped.items()):
            ordered = sorted(
                records,
                key=lambda item: (
                    item[0]["source"],
                    item[0]["relation"],
                    item[0]["target"],
                    item[0]["line"],
                ),
            )
            # Match the production architecture-node packing boundary.
            for offset in range(0, len(ordered), 25):
                segment = ordered[offset:offset + 25]
                payloads = [payload for payload, _ in segment]
                architecture_paths = sorted({
                    path
                    for _, fact_paths in segment
                    for path in fact_paths
                })
                identity = (
                    f"{kind}\0{source_path}\0{offset // 25}"
                )
                chunks.append({
                    "text": _facts_text(kind, source_path, payloads),
                    "score": 0.95,
                    "_source": "pr_indexed",
                    "_match_type": "architecture_relation",
                    "metadata": {
                        "path": (
                            "__analysis_architecture__/fixture/"
                            + hashlib.sha256(
                                identity.encode("utf-8")
                            ).hexdigest()
                            + ".context"
                        ),
                        "pr": True,
                        "architecture_key": "fixture:" + identity,
                        "architecture_kind": kind,
                        "architecture_paths": architecture_paths,
                        "plugin_graph_facts": payloads,
                    },
                })
                self.returned_fact_count += len(payloads)
        return {
            "context": {
                "chunks": chunks,
                "changed_files": {},
                "related_definitions": {},
                "_metadata": {
                    "retrieval_state": "complete",
                    "failures": [],
                },
            }
        }


def _relationship_text(selector: Mapping[str, Any]) -> str:
    return (
        f"[{selector['kind']}] {selector['source']} "
        f"{selector['relation']} {selector['target']}"
    )


async def _capture(corpus: Mapping[str, Any], digest: str) -> dict[str, Any]:
    catalog, capabilities, magento_facts, snapshots = _build_runtime(
        corpus,
        digest,
    )
    runtime = PluginRuntime(catalog)
    restored = runtime.start_repository_analysis(
        capabilities,
        digest[:40],
        snapshots=snapshots,
    )
    repository_analysis, repository_diagnostics = restored.finish()
    if repository_diagnostics:
        raise RuntimeError(
            "fixture repository replay produced diagnostics: "
            + "; ".join(
                f"{item.plugin_id or 'plugin'}:{item.code}:{item.message}"
                for item in repository_diagnostics
            )
        )
    local_facts = {
        fact
        for packet in repository_analysis.packets
        for fact in packet.facts
    }
    local_facts.update(magento_facts)
    for path, content in sorted(corpus["artifacts"].items()):
        facts, diagnostics = runtime.graph_facts(
            FileArtifact(path, content),
            capabilities,
        )
        if diagnostics:
            raise RuntimeError(
                "fixture file graph produced diagnostics: "
                + "; ".join(
                    f"{item.plugin_id or 'plugin'}:{item.code}:{item.message}"
                    for item in diagnostics
                )
            )
        local_facts.update(facts)

    raw_diff = _all_added_diff(corpus["artifacts"])
    parsed_paths = tuple(sorted(
        item.path
        for item in DiffProcessor().process(raw_diff).files
    ))
    expected_paths = tuple(sorted(corpus["artifacts"]))
    if parsed_paths != expected_paths:
        raise RuntimeError(
            "fixed prompt-gate diff does not preserve the complete file manifest: "
            f"expected={len(expected_paths)}, parsed={len(parsed_paths)}"
        )

    request = _request(corpus, capabilities, digest).model_copy(update={
        "analysisType": "PULL_REQUEST",
        "baseCommitHash": "0" * 40,
        "commitHash": digest[:40],
        "currentCommitHash": digest[:40],
        "targetBranchName": "main",
        "sourceBranchName": "fixture/magento-prompt-gate",
        "pullRequestId": 1,
        "prTitle": "Provider-free Magento prompt gate",
        "rawDiff": raw_diff,
    })
    rag = FixtureGraphRagClient(tuple(local_facts))
    report = await capture_review_prompts(
        request,
        rag,
        include_deterministic_rag=True,
        simulated_findings_per_file=0,
        full_pipeline_context=False,
    )
    report["_gate"] = {
        "plugins": list(capabilities.repository_plugins),
        "facts": tuple(sorted(local_facts)),
        "retrievalRequests": rag.requests,
        "returnedFactCount": rag.returned_fact_count,
    }
    return report


def run_gate(corpus_path: Path = DEFAULT_CORPUS) -> dict[str, Any]:
    corpus = _load_corpus(corpus_path)
    digest = hashlib.sha256(_canonical_bytes(corpus)).hexdigest()
    capture = asyncio.run(_capture(corpus, digest))
    gate_data = capture.pop("_gate")
    stage_1_prompts = [
        prompt["renderedPrompt"]
        for prompt in capture["prompts"]
        if prompt["stage"] == "stage_1"
    ]
    stage_0_prompts = [
        prompt["renderedPrompt"]
        for prompt in capture["prompts"]
        if prompt["stage"] == "stage_0"
    ]
    rendered_stage_1 = "\n".join(stage_1_prompts)
    rendered_stage_0 = "\n".join(stage_0_prompts)
    expected_relationships = {
        candidate["id"]: _relationship_text(candidate["evidenceSelector"])
        for candidate in corpus["candidates"]
        if candidate["expected"] == "publish"
        and candidate.get("evidenceSelector") is not None
    }
    missing_relationships = sorted(
        candidate_id
        for candidate_id, relationship in expected_relationships.items()
        if relationship not in rendered_stage_1
    )
    expected_php_relationships = (
        # Keep representative resolved PHP relationships in addition to the
        # exact Magento diagnostic candidates above. The fixed all-added PR
        # already requires every PHP source path to remain visible, so this
        # does not require every redundant source-derived relation to consume
        # a separate architecture entry.
        (
            "[php-constructor-dependency] "
            "Acme\\Checkout\\Model\\Service constructor-requires "
            "Acme\\Checkout\\Api\\CartInterface"
        ),
        (
            "[php-inheritance] Acme\\Checkout\\Model\\Cart implements "
            "Acme\\Checkout\\Api\\CartInterface"
        ),
        (
            "[php-construction-relation] "
            "Acme\\Checkout\\Model\\Service constructs "
            "Acme\\Checkout\\Model\\FrontendCart"
        ),
        (
            "[php-static-call-relation] "
            "Acme\\Checkout\\Model\\Service calls-static "
            "Acme\\Checkout\\Model\\Cart"
        ),
        (
            "[php-instance-call-relation] "
            "Acme\\Checkout\\Model\\Service calls-instance "
            "Acme\\Checkout\\Api\\CartInterface"
        ),
        (
            "[php-instance-call-relation] "
            "Acme\\Checkout\\Model\\Service calls-instance "
            "Acme\\Checkout\\Model\\FrontendCart"
        ),
    )
    expected_generated_factory_relationships = (
        (
            "[magento-generated-factory] "
            "Acme\\Checkout\\Model\\Service "
            "uses-generated-factory-for "
            "Acme\\Checkout\\Model\\FrontendCart"
        ),
        (
            "[magento-generated-factory-resolution] "
            "Acme\\Checkout\\Model\\Service "
            "creates-via-generated-factory "
            "Acme\\Checkout\\Model\\FrontendCart"
        ),
    )
    expected_generated_proxy_relationships = (
        (
            "[magento-generated-proxy] "
            "Acme\\Checkout\\Model\\Service "
            "injects-generated-proxy-for "
            "Acme\\Checkout\\Api\\CartInterface"
        ),
        (
            "[magento-generated-proxy-resolution] "
            "Acme\\Checkout\\Model\\Service "
            "lazy-loads-via-generated-proxy "
            "Acme\\Checkout\\Model\\Cart"
        ),
        (
            "[magento-generated-proxy-resolution] "
            "Acme\\Checkout\\Model\\Service "
            "lazy-loads-via-generated-proxy "
            "Acme\\Checkout\\Model\\FrontendCart"
        ),
    )
    missing_php_relationships = sorted(
        relationship
        for relationship in expected_php_relationships
        if relationship not in rendered_stage_1
    )
    exact_php_return_contract_visible = (
        "[php-instance-call-relation] "
        "Acme\\Checkout\\Model\\Service calls-instance "
        "Acme\\Checkout\\Api\\CartInterface"
    ) in rendered_stage_1 and (
        "targetDeclaredReturnType=void"
    ) in rendered_stage_1
    exact_php_call_return_chain_visible = (
        "[php-instance-call-relation] "
        "Acme\\Checkout\\Model\\Service calls-instance "
        "Acme\\Checkout\\Model\\FrontendCart"
    ) in rendered_stage_1 and all(
        value in rendered_stage_1
        for value in (
            "receiverResolution=exact-call-return",
            "receiverCall:0000:sourceType=Acme\\Checkout\\Model\\CartProvider",
            "receiverCall:0000:method=current",
            (
                "receiverCall:0000:declaredReturnType="
                "Acme\\Checkout\\Model\\FrontendCart"
            ),
            "targetDeclaredReturnType=void",
        )
    )
    template_global_relationship_visible = all(
        value in rendered_stage_1
        for value in (
            (
                "[magento-template-global-call] "
                "window.fixBannerExternalLinks "
                "calls-unique-co-declared-definition "
                "window.fixBannerExternalLinks"
            ),
            (
                "app/code/Acme/Checkout/view/frontend/templates/"
                "banner/helper.phtml"
            ),
            "items instanceof NodeList || Array.isArray(items)",
        )
    )
    template_event_relationship_visible = all(
        value in rendered_stage_1
        for value in (
            (
                "[magento-template-event-dispatch] "
                "window:banner-ready "
                "dispatches-to-unique-layout-listener "
                "window:banner-ready"
            ),
            "resolution=shared-layout-source",
            (
                "app/code/Acme/Checkout/view/frontend/templates/"
                "banner/helper.phtml"
            ),
            "window.addEventListener('banner-ready'",
        )
    )
    hyva_template_webapi_context_visible = all(
        value in rendered_stage_1
        for value in (
            (
                "[hyva-template-webapi-reference] "
                "app/design/frontend/Acme/custom/Acme_Sales/"
                "templates/orders/init.phtml "
                "references-exact-webapi-route-literal "
                "POST /V1/acme/orders/list/"
            ),
            (
                "app/design/frontend/Acme/custom/Acme_Sales/"
                "templates/orders/items.phtml"
            ),
            "app/code/Acme/Sales/Model/OrderInfo.php",
            "app/code/Acme/Sales/ViewModel/Orders.php",
            "app/code/Acme/Sales/Model/OrderItemsProcessor.php",
            "service=Acme\\Sales\\Api\\OrderInfoInterface::getOrderInfo",
            "implementation=Acme\\Sales\\Model\\OrderInfo",
            "resolution=exact-registry-route-literal",
            "retrievalIdentifier:0000=getOrderInfo",
            "retrievalIdentifier:0001=prepareOnlineOrders",
            "retrievalIdentifier:0002=prepareOrdersData",
            "retrievalIdentifier:0003=process",
            "'display_price' => '10.00'",
            'x-text="order.display_price"',
        )
    )
    hyva_alpine_component_context_visible = all(
        value in rendered_stage_1
        for value in (
            (
                "[hyva-alpine-component-reference] "
                "app/design/frontend/Acme/custom/Acme_Sales/"
                "templates/orders/items.phtml "
                "uses-exact-alpine-provider initOrderClipboard"
            ),
            "factoryName=initOrderClipboard",
            "resolution=exact-alpine-data-named-factory",
            "function initOrderClipboard()",
            'x-data="initOrderClipboard()"',
            "copyOrder()",
            'x-text="copied"',
        )
    )
    hyva_alpine_event_context_visible = all(
        value in rendered_stage_1
        for value in (
            (
                "[hyva-alpine-event-dispatch] "
                "app/design/frontend/Acme/custom/Acme_Sales/"
                "templates/orders/init.phtml "
                "dispatches-to-exact-alpine-listener order-copied"
            ),
            "resolution=exact-layout-window-event",
            "listenerCount=1",
            "$dispatch('order-copied'",
            "@order-copied.window",
        )
    )
    hyva_runtime_variable_context_visible = all(
        value in rendered_stage_1
        for value in (
            (
                "[hyva-template-runtime-variable] "
                "app/design/frontend/Acme/custom/Acme_Sales/"
                "templates/orders/init.phtml "
                "receives-hyva-runtime-variable "
                "Hyva\\Theme\\ViewModel\\HyvaCsp"
            ),
            "resolution=hyva-theme-template-contract",
            "theme=Acme/custom",
            "variable=$hyvaCsp",
            "$hyvaCsp->registerInlineScript()",
            "app/design/frontend/Acme/custom/theme.xml",
        )
    )
    hyva_evidence_contract_visible = all(
        all(value in rendered for value in (
            (
                "For a Hyva template-runtime, cross-template ViewModel, REST, "
                "Alpine provider"
            ),
            "use that relationship kind as claimKind",
            "a topology relationship proves presence and context",
            "not that the relationship is defective",
        ))
        for rendered in (rendered_stage_0, rendered_stage_1)
    )
    price_pool_context_visible = all(
        value in rendered_stage_1
        for value in (
            (
                "[magento-price-pool-reference] "
                "Acme\\Sales\\Model\\OrderItemsProcessor "
                "requests-registered-price "
                "Acme\\Sales\\Model\\WeightPrice"
            ),
            (
                "[magento-price-pool-registration] "
                "Magento\\Catalog\\Pricing\\Price\\Pool "
                "registers-price-model "
                "Acme\\Sales\\Model\\WeightPrice"
            ),
            "priceCode=price_per_weight",
            "app/code/Acme/Sales/etc/di.xml",
            (
                "app/code/Acme/Sales/Model/"
                "OrderItemsProcessor.php"
            ),
        )
    )
    missing_generated_factory_relationships = sorted(
        relationship
        for relationship in expected_generated_factory_relationships
        if relationship not in rendered_stage_1
    )
    missing_generated_proxy_relationships = sorted(
        relationship
        for relationship in expected_generated_proxy_relationships
        if relationship not in rendered_stage_1
    )
    php_paths = sorted(
        path for path in corpus["artifacts"]
        if path.casefold().endswith((".php", ".phtml", ".inc"))
    )
    missing_php_targets = sorted(
        path for path in php_paths
        if path not in rendered_stage_1
    )
    facts = gate_data["facts"]
    fact_kinds = Counter(fact.kind for fact in facts)
    inbound_queue_topic = "acme.cart.save"
    publisherless_inbound_handler_visible = (
        any(
            fact.kind == "magento-message-effective-handler"
            and fact.source == inbound_queue_topic
            and fact.target
            == "Acme\\Checkout\\Model\\QueueHandler::process"
            for fact in facts
        )
        and not any(
            fact.kind == "magento-message-publisher"
            and fact.source == inbound_queue_topic
            for fact in facts
        )
        and (
            "[magento-message-effective-handler] "
            "acme.cart.save handled-by "
            "Acme\\Checkout\\Model\\QueueHandler::process"
        ) in rendered_stage_1
    )
    prompt_digest = stable_prompt_digest(capture["prompts"])
    quality = capture["qualitySignals"]["stage1"]
    targets = corpus["qualityTargets"]
    checks = {
        "providerCalls": capture["providerCalls"] == 0,
        "phpMagentoHyvaSelected": (
            {"php", "magento", "hyva"} <= set(gate_data["plugins"])
        ),
        "deterministicRetrievalUsed": bool(gate_data["retrievalRequests"]),
        "exactFactsReturned": gate_data["returnedFactCount"] > 0,
        "expectedMagentoRelationshipsVisible": not missing_relationships,
        "publisherlessInboundHandlerVisible": (
            publisherless_inbound_handler_visible
        ),
        "expectedPhpCodeRelationshipsVisible": not missing_php_relationships,
        "exactPhpTargetReturnContractVisible": (
            exact_php_return_contract_visible
        ),
        "exactPhpCallReturnChainVisible": (
            exact_php_call_return_chain_visible
        ),
        "templateGlobalRelationshipVisible": (
            template_global_relationship_visible
        ),
        "templateEventRelationshipVisible": (
            template_event_relationship_visible
        ),
        "hyvaTemplateWebapiContextVisible": (
            hyva_template_webapi_context_visible
        ),
        "hyvaAlpineComponentContextVisible": (
            hyva_alpine_component_context_visible
        ),
        "hyvaAlpineEventContextVisible": (
            hyva_alpine_event_context_visible
        ),
        "hyvaRuntimeVariableContextVisible": (
            hyva_runtime_variable_context_visible
        ),
        "hyvaEvidenceContractVisible": hyva_evidence_contract_visible,
        "pricePoolContextVisible": price_pool_context_visible,
        "expectedGeneratedFactoryRelationshipsVisible": (
            not missing_generated_factory_relationships
        ),
        "expectedGeneratedProxyRelationshipsVisible": (
            not missing_generated_proxy_relationships
        ),
        "phpEvidenceTargetsVisible": not missing_php_targets,
        "noHiddenPluginEvidenceTargets": (
            quality["hiddenPluginEvidenceTargets"] == 0
        ),
        "promptInputTokenCeiling": (
            capture["estimatedTotalInputTokens"]
            <= int(targets["maxPromptEstimatedInputTokens"])
        ),
        "stage1InputTokenCeiling": (
            quality["maxEstimatedInputTokens"]
            <= int(targets["maxStage1EstimatedInputTokens"])
        ),
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "corpusId": corpus["corpusId"],
        "corpusSha256": digest,
        "checks": checks,
        "plugins": gate_data["plugins"],
        "architecture": {
            "factCount": len(facts),
            "factKinds": dict(sorted(fact_kinds.items())),
            "retrievalRequests": len(gate_data["retrievalRequests"]),
            "returnedFactCount": gate_data["returnedFactCount"],
        },
        "prompt": {
            "digest": prompt_digest,
            "recordDigests": stable_prompt_record_digests(
                capture["prompts"]
            ),
            "count": capture["promptCount"],
            "countsByStage": capture["promptCountsByStage"],
            "estimatedTotalInputTokens": capture["estimatedTotalInputTokens"],
            "qualitySignals": capture["qualitySignals"],
        },
        "missingExpectedRelationships": missing_relationships,
        "missingPhpCodeRelationships": missing_php_relationships,
        "missingGeneratedFactoryRelationships": (
            missing_generated_factory_relationships
        ),
        "missingGeneratedProxyRelationships": (
            missing_generated_proxy_relationships
        ),
        "missingPhpEvidenceTargets": missing_php_targets,
        "providerCalls": capture["providerCalls"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus", nargs="?", type=Path, default=DEFAULT_CORPUS)
    arguments = parser.parse_args()
    try:
        report = run_gate(arguments.corpus)
    except Exception as exception:
        report = {
            "status": "failed",
            "error": f"{type(exception).__name__}: {exception}",
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Test-only review execution that captures prompts without provider calls."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from model.dtos import ReviewRequestDto
from model.multi_stage import (
    CrossFileAnalysisResult,
    FileGroup,
    FileReviewBatchOutput,
    FileReviewOutput,
    ReviewFile,
    ReviewPlan,
)
from model.output_schemas import (
    CodeReviewIssue,
    CodeReviewOutput,
    DeduplicatedIssueList,
    ReconciliationOutput,
)
from llm.provider_guard import forbid_llm_provider_construction
from service.rag.llm_reranker import LLMReranker, RerankResponse
from service.review.orchestrator import MultiStageReviewOrchestrator
from service.review.plugin_context import (
    capture_plugin_diagnostics,
)
from service.review.prompt_diagnostics import capture_prompt_diagnostics
from service.review.evidence_scopes import process_review_evidence_scopes


_FILE_SECTION = re.compile(
    r"^FILE #\d+:\s*(?P<path>[^\r\n]+).*?"
    r"Current File Content \(post-change; may be bounded when explicitly labelled\):\s*\n"
    r"(?P<content>.*?)\n\n(?:Delta )?Diff:\s*\n(?P<diff>.*?)(?=\n---(?:\n|$)|\Z)",
    re.MULTILINE | re.DOTALL,
)
_RERANK_ID = re.compile(r'"id"\s*:\s*(\d+)')
_HIDDEN_PLUGIN_EVIDENCE = re.compile(
    r"\[(?P<count>\d+) plugin evidence target\(s\) omitted because "
    r"no matching exact fact is visible"
)


@dataclass
class CapturedAIMessage:
    """Provider-independent response shape used by the real orchestration flow."""

    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    response_metadata: dict[str, Any] = field(default_factory=dict)
    type: str = "ai"


def _message_payload(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        return {
            str(key): _json_safe(value)
            for key, value in message.items()
        }
    payload: dict[str, Any] = {
        "type": getattr(message, "type", message.__class__.__name__),
        "content": _json_safe(getattr(message, "content", str(message))),
    }
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        payload["tool_calls"] = _json_safe(tool_calls)
    return payload


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    return str(value)


def _serialize_input(input_data: Any) -> tuple[str, Any]:
    if isinstance(input_data, str):
        return input_data, input_data
    if isinstance(input_data, (list, tuple)):
        messages = [_message_payload(message) for message in input_data]
        rendered = "\n\n".join(
            f"[{message.get('role') or message.get('type') or 'message'}]\n"
            f"{message.get('content', '')}"
            for message in messages
        )
        return rendered, messages
    rendered = json.dumps(_json_safe(input_data), ensure_ascii=False, indent=2)
    return rendered, _json_safe(input_data)


def _schema_name(schema: Any) -> Optional[str]:
    return getattr(schema, "__name__", None) if schema is not None else None


def _schema_definition(schema: Any) -> Optional[dict[str, Any]]:
    if schema is None:
        return None
    if hasattr(schema, "model_json_schema"):
        return _json_safe(schema.model_json_schema())
    return {"name": _schema_name(schema) or str(schema)}


def _classify_stage(schema: Any, rendered: str, tools: tuple[dict[str, Any], ...]) -> str:
    by_schema = {
        "ReviewPlan": "stage_0",
        "FileReviewBatchOutput": "stage_1",
        "CrossFileAnalysisResult": "stage_2",
        "DeduplicatedIssueList": "deduplication",
        "ReconciliationOutput": "branch_reconciliation",
        "CodeReviewOutput": "branch_analysis",
        "RerankResponse": "rag_reranking",
    }
    if _schema_name(schema) in by_schema:
        return by_schema[_schema_name(schema)]
    if tools and "Verification Agent" in rendered:
        return "verification"
    if "JSON repair expert" in rendered:
        return "json_repair"
    if "Produce final PR executive summary" in rendered:
        return "stage_3"
    if "reconciliation" in rendered.casefold():
        return "branch_reconciliation"
    return "unclassified"


def _tool_descriptor(tool: Any) -> dict[str, Any]:
    if isinstance(tool, dict):
        return _json_safe(tool)
    name = getattr(tool, "name", None) or getattr(tool, "__name__", tool.__class__.__name__)
    descriptor: dict[str, Any] = {"name": str(name)}
    description = getattr(tool, "description", None) or getattr(tool, "__doc__", None)
    if description:
        descriptor["description"] = str(description).strip()
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is not None and hasattr(args_schema, "model_json_schema"):
        descriptor["input_schema"] = args_schema.model_json_schema()
    return descriptor


def _candidate_anchor(content: str, diff: str) -> str:
    for line in content.splitlines():
        candidate = line.strip()
        if candidate and not candidate.startswith(("[", "```")):
            return candidate
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            candidate = line[1:].strip()
            if candidate:
                return candidate
    return ""


@dataclass
class PromptCaptureSession:
    """Shared state behind every bound view of the capture LLM."""

    request: ReviewRequestDto
    simulated_findings_per_file: int = 0
    simulated_findings_max_total: int = 24
    prompts: list[dict[str, Any]] = field(default_factory=list)
    pipeline_events: list[dict[str, Any]] = field(default_factory=list)
    plugin_diagnostics: list[dict[str, str]] = field(default_factory=list)
    prompt_assembly_diagnostics: list[dict[str, Any]] = field(
        default_factory=list
    )
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _synthetic_finding_ids: set[str] = field(default_factory=set)

    def _simulated_finding_count(self, path: str) -> int:
        """Allocate a bounded total deterministically across changed files."""
        paths = tuple(sorted(set(self.request.changedFiles or [])))
        if (
            self.simulated_findings_per_file <= 0
            or self.simulated_findings_max_total <= 0
            or path not in paths
        ):
            return 0
        total = min(
            self.simulated_findings_max_total,
            self.simulated_findings_per_file * len(paths),
        )
        base, remainder = divmod(total, len(paths))
        rank = paths.index(path)
        return min(
            self.simulated_findings_per_file,
            base + (1 if rank < remainder else 0),
        )

    async def record(
        self,
        input_data: Any,
        schema: Any,
        include_raw: bool,
        bindings: dict[str, Any],
        tools: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], str]:
        rendered, serialized = _serialize_input(input_data)
        stage = _classify_stage(schema, rendered, tools)
        schema_definition = _schema_definition(schema)
        declarations: dict[str, Any] = {}
        if schema_definition is not None:
            declarations["responseSchema"] = schema_definition
        if tools:
            declarations["tools"] = list(tools)
        provider_declarations = json.dumps(
            declarations,
            ensure_ascii=False,
            separators=(",", ":"),
        ) if declarations else ""
        request_character_count = len(rendered) + len(provider_declarations)
        async with self._lock:
            sequence = len(self.prompts) + 1
            record = {
                "sequence": sequence,
                "stage": stage,
                "callType": (
                    "tool-enabled"
                    if tools
                    else "structured"
                    if schema is not None
                    else "raw"
                ),
                "responseSchema": _schema_name(schema),
                "responseSchemaDefinition": schema_definition,
                "includeRawResponse": include_raw,
                "modelBindings": _json_safe(bindings),
                "tools": list(tools),
                "input": serialized,
                "renderedPrompt": rendered,
                "renderedPromptCharacterCount": len(rendered),
                "characterCount": request_character_count,
                "estimatedInputTokens": math.ceil(request_character_count / 4),
            }
            self.prompts.append(record)
        return record, rendered

    def response_for(self, schema: Any, rendered: str, include_raw: bool) -> Any:
        response = self._structured_response(schema, rendered)
        if include_raw:
            return {
                "parsed": response,
                "raw": CapturedAIMessage(content=json.dumps(_json_safe(response))),
                "parsing_error": None,
            }
        return response

    def raw_response_for(self, stage: str) -> CapturedAIMessage:
        if stage == "verification":
            content = '{"issue_ids_to_drop":[]}'
        elif stage in {"branch_analysis", "branch_reconciliation"}:
            content = '{"comment":"Dry-run simulated response.","issues":[]}'
        else:
            content = "[dry-run simulated response omitted]"
        return CapturedAIMessage(content=content)

    def _structured_response(self, schema: Any, rendered: str) -> Any:
        if schema is ReviewPlan:
            paths = list(dict.fromkeys(self.request.changedFiles or []))
            return ReviewPlan(
                analysis_summary="Dry-run synthetic plan used only to construct downstream prompts.",
                file_groups=[
                    FileGroup(
                        group_id="dry-run",
                        priority="HIGH" if self.simulated_findings_per_file else "MEDIUM",
                        rationale="All supplied files are retained for prompt capture.",
                        files=[
                            ReviewFile(
                                path=path,
                                focus_areas=["general review"],
                                risk_level="MEDIUM",
                            )
                            for path in paths
                        ],
                    )
                ] if paths else [],
                files_to_skip=[],
                cross_file_concerns=(
                    ["Dry-run synthetic findings require cross-file prompt construction."]
                    if self.simulated_findings_per_file
                    else []
                ),
            )

        if schema is FileReviewBatchOutput:
            reviews = []
            for section in _FILE_SECTION.finditer(rendered):
                path = section.group("path").strip()
                anchor = _candidate_anchor(section.group("content"), section.group("diff"))
                issues = []
                if anchor:
                    for index in range(self._simulated_finding_count(path)):
                        marker = hashlib.sha256(
                            f"{path}:{index}".encode("utf-8")
                        ).hexdigest()
                        finding_id = f"dry-run-{path}-{index}"
                        self._synthetic_finding_ids.add(finding_id)
                        issues.append(CodeReviewIssue(
                            id=finding_id,
                            severity="HIGH",
                            category="BUG_RISK",
                            file=path,
                            line=1,
                            scope="LINE",
                            title=f"Dry-run synthetic finding {index + 1} in {path}",
                            reason=(
                                f"Synthetic evidence marker {marker} for {path}; "
                                "used only to construct conditional dry-run prompts."
                            ),
                            suggestedFixDescription=(
                                "Synthetic required change used only for dry-run prompt construction."
                            ),
                            codeSnippet=anchor,
                        ))
                reviews.append(FileReviewOutput(
                    file=path,
                    analysis_summary="Dry-run simulated review output.",
                    issues=issues,
                    confidence="HIGH",
                ))
            return FileReviewBatchOutput(reviews=reviews)

        if schema is CrossFileAnalysisResult:
            return CrossFileAnalysisResult(
                pr_risk_level="LOW",
                cross_file_issues=[],
                pr_recommendation="Dry-run simulated recommendation.",
                confidence="HIGH",
            )

        if schema is DeduplicatedIssueList:
            return DeduplicatedIssueList(kept_indices=list(range(10_000)))

        if schema is ReconciliationOutput:
            return ReconciliationOutput(
                comment="Dry-run simulated reconciliation.",
                issues=[],
            )

        if schema is CodeReviewOutput:
            return CodeReviewOutput(
                comment="Dry-run simulated branch analysis.",
                issues=[],
            )

        if schema is RerankResponse:
            rankings = sorted({int(value) for value in _RERANK_ID.findall(rendered)})
            return RerankResponse(
                rankings=rankings,
                reasoning="Dry-run preserves the supplied order.",
            )

        if schema is not None and hasattr(schema, "model_validate"):
            return schema.model_validate({})
        raise TypeError(f"unsupported dry-run response schema: {_schema_name(schema)}")

    def report(
        self,
        provider: str,
        model: str,
        deterministic_rag_requests: Optional[int],
        deterministic_rag_enabled: bool,
        mcp_requested: bool,
        full_pipeline_context: bool,
    ) -> dict[str, Any]:
        counts = Counter(prompt["stage"] for prompt in self.prompts)
        total_chars = sum(prompt["characterCount"] for prompt in self.prompts)
        total_tokens = sum(prompt["estimatedInputTokens"] for prompt in self.prompts)
        stage_1_prompts = [
            prompt for prompt in self.prompts
            if prompt["stage"] == "stage_1"
        ]
        hidden_plugin_counts = [
            int(match.group("count"))
            for prompt in stage_1_prompts
            for match in _HIDDEN_PLUGIN_EVIDENCE.finditer(
                prompt["renderedPrompt"]
            )
        ]
        stage_1_quality_signals = {
            "promptCount": len(stage_1_prompts),
            "totalEstimatedInputTokens": sum(
                prompt["estimatedInputTokens"]
                for prompt in stage_1_prompts
            ),
            "maxEstimatedInputTokens": max(
                (
                    prompt["estimatedInputTokens"]
                    for prompt in stage_1_prompts
                ),
                default=0,
            ),
            "ragEvidenceEntries": sum(
                prompt["renderedPrompt"].count("Evidence ID: RAG-")
                for prompt in stage_1_prompts
            ),
            "promptsWithHiddenPluginEvidence": len(
                [
                    prompt
                    for prompt in stage_1_prompts
                    if _HIDDEN_PLUGIN_EVIDENCE.search(
                        prompt["renderedPrompt"]
                    )
                ]
            ),
            "hiddenPluginEvidenceTargets": sum(
                hidden_plugin_counts
            ),
            "ragContextTruncationMarkers": sum(
                prompt["renderedPrompt"].count(
                    "Context chunk truncated by deterministic prompt budget"
                )
                for prompt in stage_1_prompts
            ),
            "currentSourceTruncationMarkers": sum(
                prompt["renderedPrompt"].count(
                    "Current file context truncated"
                )
                for prompt in stage_1_prompts
            ),
            "postChangeSourceWindows": sum(
                prompt["renderedPrompt"].count(
                    "Post-change source windows around reviewed diff hunks"
                )
                for prompt in stage_1_prompts
            ),
            "addedSourceDuplicateOmissions": sum(
                prompt["renderedPrompt"].count(
                    "Complete post-change source is present once as the added "
                    "side of the diff below"
                )
                for prompt in stage_1_prompts
            ),
        }
        stage_1_assembly = [
            item
            for item in self.prompt_assembly_diagnostics
            if item.get("stage") == "stage_1"
        ]
        stage_1_prompt_characters = sum(
            prompt["renderedPromptCharacterCount"]
            for prompt in stage_1_prompts
        )
        for field_name, signal_name in (
            ("currentSourceChars", "currentSourceCharacters"),
            ("diffChars", "diffCharacters"),
            ("metadataChars", "metadataCharacters"),
            ("ragChars", "ragContextCharacters"),
            ("pluginChars", "pluginContextCharacters"),
            ("projectRulesChars", "projectRulesCharacters"),
            ("taskContextChars", "taskContextCharacters"),
            ("previousIssuesChars", "previousIssuesCharacters"),
        ):
            stage_1_quality_signals[signal_name] = sum(
                int(item.get(field_name) or 0)
                for item in stage_1_assembly
            )
        stage_1_quality_signals["pluginContextShare"] = round(
            stage_1_quality_signals["pluginContextCharacters"]
            / stage_1_prompt_characters,
            6,
        ) if stage_1_prompt_characters else 0.0
        stage_1_quality_signals["ragContextShare"] = round(
            stage_1_quality_signals["ragContextCharacters"]
            / stage_1_prompt_characters,
            6,
        ) if stage_1_prompt_characters else 0.0
        event_states = [
            str(event["state"])
            for event in self.pipeline_events
            if event.get("type") == "status" and event.get("state")
        ]
        completion_event = next(
            (
                event
                for event in reversed(self.pipeline_events)
                if event.get("state") == "review_evidence_completed"
            ),
            None,
        )
        exception_plugin_diagnostics = [
            item
            for item in self.plugin_diagnostics
            if "exception" in str(item.get("code") or "").casefold()
        ]
        warnings = [
            (
                "Captured prompts follow synthetic schema-valid responses. Later "
                "prompts in a real review vary with earlier model outputs."
            ),
            (
                "Provider-error retries, structured-output fallbacks, and JSON-repair "
                "prompts are absent unless their triggering failure occurs."
            ),
        ]
        if full_pipeline_context:
            warnings.insert(
                0,
                (
                    "The review LLM provider was disabled. Normal deterministic and "
                    "semantic RAG retrieval plus PR overlay indexing remained active "
                    "so the captured prompts contain real assembled project context."
                ),
            )
        else:
            warnings.insert(
                0,
                (
                    "Provider and embedding calls were disabled. Semantic and duplication "
                    "RAG context is intentionally absent; deterministic indexed context "
                    "is included when requested and available."
                ),
            )
        if self.simulated_findings_per_file == 0:
            warnings.append(
                "Verification and LLM deduplication prompts are conditional on findings "
                "and are therefore absent in the zero-findings baseline. Set "
                "simulatedFindingsPerFile above zero to exercise those paths."
            )
        if mcp_requested:
            warnings.append(
                "useMcpTools was disabled for dry-run safety; iterative tool-call "
                "prompts cannot be predicted without executing tool responses."
            )
        return {
            "dryRun": True,
            "providerCalls": 0,
            "providerCallsScope": "review-llm-only",
            "embeddingProviderCallsMeasured": False,
            "providerConstructionGuard": {
                "enabled": True,
                "boundary": "LLMFactory.create_llm",
            },
            "provider": provider,
            "model": model,
            "simulation": {
                "simulatedFindingsPerFile": self.simulated_findings_per_file,
                "simulatedFindingsMaxTotal": self.simulated_findings_max_total,
                "simulatedFindingsProduced": len(self._synthetic_finding_ids),
                "fullPipelineContext": full_pipeline_context,
                "deterministicRagEnabled": deterministic_rag_enabled,
                "deterministicRagRequests": deterministic_rag_requests,
                "semanticRagEnabled": full_pipeline_context,
                "duplicationRagEnabled": full_pipeline_context,
                "prIndexMutationEnabled": full_pipeline_context,
                "mcpToolsEnabled": False,
            },
            "promptCount": len(self.prompts),
            "promptCountsByStage": dict(sorted(counts.items())),
            "totalCharacterCount": total_chars,
            "estimatedTotalInputTokens": total_tokens,
            "qualitySignals": {
                "stage1": stage_1_quality_signals,
            },
            "reviewIdentity": {
                "projectId": self.request.projectId,
                "analysisType": self.request.analysisType,
                "pullRequestId": self.request.pullRequestId,
                "targetBranch": self.request.targetBranchName,
                "sourceBranch": self.request.sourceBranchName,
                "headRevision": (
                    self.request.currentCommitHash or self.request.commitHash
                ),
                "baseRevision": self.request.baseCommitHash,
                "changedFiles": sorted(set(self.request.changedFiles or ())),
                "deletedFiles": sorted(set(self.request.deletedFiles or ())),
                "rawDiffSha256": (
                    hashlib.sha256(self.request.rawDiff.encode("utf-8")).hexdigest()
                    if self.request.rawDiff is not None
                    else None
                ),
            },
            "pipeline": {
                "completed": completion_event is not None,
                "eventStates": event_states,
                "evidence": completion_event,
                "events": self.pipeline_events,
            },
            "pluginDiagnostics": {
                "count": len(self.plugin_diagnostics),
                "exceptionCount": len(exception_plugin_diagnostics),
                "items": self.plugin_diagnostics,
            },
            "promptAssemblyDiagnostics": {
                "stage1": stage_1_assembly,
            },
            "tokenEstimateMethod": (
                "ceil((rendered prompt + response/tool schema JSON) characters / 4) "
                "per invocation"
            ),
            "warnings": warnings,
            "prompts": self.prompts,
        }


class PromptCaptureLLM:
    """Small LangChain-compatible surface that never owns a provider client."""

    def __init__(
        self,
        session: PromptCaptureSession,
        *,
        schema: Any = None,
        include_raw: bool = False,
        bindings: Optional[dict[str, Any]] = None,
        tools: Iterable[dict[str, Any]] = (),
    ):
        self._session = session
        self._schema = schema
        self._include_raw = include_raw
        self._bindings = dict(bindings or {})
        self._tools = tuple(tools)
        self.model_kwargs = dict(self._bindings.get("model_kwargs") or {})
        self.max_tokens = self._bindings.get("max_tokens")

    def _clone(self, **updates: Any) -> "PromptCaptureLLM":
        return PromptCaptureLLM(
            self._session,
            schema=updates.get("schema", self._schema),
            include_raw=updates.get("include_raw", self._include_raw),
            bindings=updates.get("bindings", self._bindings),
            tools=updates.get("tools", self._tools),
        )

    def model_copy(self, update: Optional[dict[str, Any]] = None, **_: Any) -> "PromptCaptureLLM":
        bindings = dict(self._bindings)
        bindings.update(update or {})
        return self._clone(bindings=bindings)

    def copy(self, update: Optional[dict[str, Any]] = None, **kwargs: Any) -> "PromptCaptureLLM":
        return self.model_copy(update=update, **kwargs)

    def bind(self, **kwargs: Any) -> "PromptCaptureLLM":
        bindings = dict(self._bindings)
        bindings.update(kwargs)
        return self._clone(bindings=bindings)

    def with_structured_output(
        self,
        schema: Any,
        include_raw: bool = False,
        **_: Any,
    ) -> "PromptCaptureLLM":
        return self._clone(schema=schema, include_raw=include_raw)

    def bind_tools(self, tools: Iterable[Any], **_: Any) -> "PromptCaptureLLM":
        return self._clone(tools=tuple(_tool_descriptor(tool) for tool in tools))

    async def ainvoke(self, input_data: Any, **_: Any) -> Any:
        record, rendered = await self._session.record(
            input_data,
            self._schema,
            self._include_raw,
            self._bindings,
            self._tools,
        )
        if self._schema is not None:
            return self._session.response_for(
                self._schema,
                rendered,
                self._include_raw,
            )
        return self._session.raw_response_for(record["stage"])


class DeterministicOnlyRagClient:
    """Read-only RAG facade used by dry runs.

    Exact metadata retrieval is safe because it does not generate embeddings.
    Every mutation and every embedding-backed search path is replaced locally.
    """

    def __init__(
        self,
        delegate: Any,
        enabled: bool,
        project_capabilities: Any = None,
    ):
        self._delegate = delegate
        self._enabled = enabled
        self._project_capabilities = project_capabilities
        self.deterministic_requests = 0

    async def get_deterministic_context(self, **kwargs: Any) -> dict[str, Any]:
        if not self._enabled or self._delegate is None:
            return {"context": {"chunks": [], "changed_files": {}, "related_definitions": {}}}
        self.deterministic_requests += 1
        return await self._delegate.get_deterministic_context(**kwargs)

    async def get_pr_context(self, **_: Any) -> dict[str, Any]:
        return {"context": {"relevant_code": []}}

    async def search_for_duplicates(self, **_: Any) -> list[dict[str, Any]]:
        return []

    async def index_pr_files(self, **_: Any) -> dict[str, Any]:
        # Report the shape expected by the orchestrator so it follows the same
        # post-index prompt path, while keeping the operation entirely local.
        effective = None
        if self._project_capabilities is not None:
            from service.review.plugin_context import _plugin_host

            host = _plugin_host()
            if host is None:
                raise RuntimeError(
                    "dry-run plugin projection requires the plugin runtime"
                )
            catalog, _, _ = host
            plugin_ids = tuple(
                self._project_capabilities.repositoryPlugins
            )
            effective = self._project_capabilities.model_dump()
            effective["implementationFingerprint"] = (
                catalog.implementation_fingerprint(plugin_ids)
            )
        return {
            "status": "indexed",
            "chunks_indexed": 0,
            "effective_project_capabilities": effective,
        }

    async def delete_pr_files(self, **_: Any) -> dict[str, Any]:
        return {"status": "dry_run_skipped"}


async def capture_review_prompts(
    request: ReviewRequestDto,
    rag_client: Any,
    *,
    include_deterministic_rag: bool = True,
    simulated_findings_per_file: int = 0,
    simulated_findings_max_total: int = 24,
    full_pipeline_context: bool = False,
    event_callback: Optional[Callable[[dict[str, Any]], None]] = None,
) -> dict[str, Any]:
    """Execute prompt construction with schema-valid synthetic responses."""
    if simulated_findings_per_file < 0 or simulated_findings_per_file > 10:
        raise ValueError("simulatedFindingsPerFile must be between 0 and 10")
    if simulated_findings_max_total < 1 or simulated_findings_max_total > 200:
        raise ValueError("simulatedFindingsMaxTotal must be between 1 and 200")
    if request.analysisType == "BRANCH_ANALYSIS" and request.previousCodeAnalysisIssues:
        if not request.reconciliationFileContents:
            raise ValueError(
                "branch reconciliation dry runs require reconciliationFileContents "
                "because MCP access is disabled"
            )

    safe_request = request.model_copy(update={"useMcpTools": False})
    full_pipeline_rag_enabled = (
        full_pipeline_context
        and rag_client is not None
        and bool(getattr(rag_client, "enabled", True))
    )
    session = PromptCaptureSession(
        request=safe_request,
        simulated_findings_per_file=simulated_findings_per_file,
        simulated_findings_max_total=simulated_findings_max_total,
    )
    llm = PromptCaptureLLM(session)

    def capture_event(event: dict[str, Any]) -> None:
        session.pipeline_events.append(_json_safe(event))
        if event_callback is not None:
            event_callback(event)

    with (
        forbid_llm_provider_construction("review prompt dry run"),
        capture_plugin_diagnostics(session.plugin_diagnostics.append),
        capture_prompt_diagnostics(
            session.prompt_assembly_diagnostics.append
        ),
    ):
        dry_rag = (
            rag_client
            if full_pipeline_context
            else DeterministicOnlyRagClient(
                rag_client,
                include_deterministic_rag,
                safe_request.projectCapabilities,
            )
        )
        orchestrator = MultiStageReviewOrchestrator(
            llm=llm,
            mcp_client=None,
            rag_client=dry_rag,
            event_callback=capture_event,
            llm_reranker=LLMReranker(llm_client=llm),
        )

        if (
            safe_request.analysisType == "BRANCH_ANALYSIS"
            and safe_request.previousCodeAnalysisIssues
        ):
            await orchestrator.execute_batched_branch_analysis(
                safe_request,
                {
                    "branch": safe_request.get_rag_branch(),
                    "baseBranch": safe_request.get_rag_base_branch(),
                    "commitHash": safe_request.commitHash,
                    "pullRequestId": safe_request.pullRequestId,
                    "repoSlug": safe_request.projectVcsRepoSlug,
                    "workspace": safe_request.projectVcsWorkspace,
                    "previousCodeAnalysisIssues": [
                        issue.model_dump(by_alias=True, exclude_none=True)
                        for issue in safe_request.previousCodeAnalysisIssues or []
                    ],
                },
            )
        else:
            evidence_scopes = process_review_evidence_scopes(safe_request)
            await orchestrator.orchestrate_review(
                request=safe_request,
                rag_context=None,
                processed_diff=evidence_scopes.review,
                full_pr_processed_diff=evidence_scopes.full_pr,
            )

    return session.report(
        provider=request.aiProvider,
        model=request.aiModel,
        deterministic_rag_requests=(
            None if full_pipeline_context else dry_rag.deterministic_requests
        ),
        deterministic_rag_enabled=(
            full_pipeline_rag_enabled
            if full_pipeline_context
            else include_deterministic_rag
        ),
        mcp_requested=bool(request.useMcpTools),
        full_pipeline_context=full_pipeline_context,
    )


def _artifact_component(value: Any, fallback: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip("-._")
    return normalized[:80] or fallback


async def capture_and_store_review_prompts(
    request: ReviewRequestDto,
    rag_client: Any,
    *,
    simulated_findings_per_file: int = 6,
    simulated_findings_max_total: int = 24,
    event_callback: Optional[Callable[[dict[str, Any]], None]] = None,
) -> dict[str, Any]:
    """Capture prompts through the real context pipeline and persist one artifact."""
    report = await capture_review_prompts(
        request,
        rag_client,
        include_deterministic_rag=True,
        simulated_findings_per_file=simulated_findings_per_file,
        simulated_findings_max_total=simulated_findings_max_total,
        full_pipeline_context=True,
        event_callback=event_callback,
    )

    output_dir = Path(os.environ.get(
        "ANALYSIS_PROMPT_DRY_RUN_OUTPUT_DIR",
        "/app/logs/prompt-dry-runs",
    )).expanduser()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    project = _artifact_component(request.projectId, "unknown-project")
    pull_request = _artifact_component(request.pullRequestId, "branch")
    commit = _artifact_component(
        request.currentCommitHash or request.commitHash,
        "unknown-commit",
    )[:16]
    run_id = _artifact_component(
        request.promptDryRunId or uuid.uuid4().hex,
        uuid.uuid4().hex,
    )
    filename = (
        f"{timestamp}_project-{project}_pr-{pull_request}_"
        f"commit-{commit}_job-{run_id}.json"
    )
    artifact_path = output_dir / filename
    payload = json.dumps(report, ensure_ascii=False, indent=2)

    def _write_atomically() -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        temporary = artifact_path.with_suffix(".json.tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, artifact_path)

    _write_atomically()
    artifact = {
        "filename": filename,
        "containerPath": str(artifact_path),
        "promptCount": report["promptCount"],
        "promptCountsByStage": report["promptCountsByStage"],
        "estimatedTotalInputTokens": report["estimatedTotalInputTokens"],
        "qualitySignals": report["qualitySignals"],
        "providerCalls": report["providerCalls"],
        "providerCallsScope": report["providerCallsScope"],
        "embeddingProviderCallsMeasured": report[
            "embeddingProviderCallsMeasured"
        ],
        "providerConstructionGuard": report["providerConstructionGuard"],
        "pipeline": {
            "completed": report["pipeline"]["completed"],
            "eventStates": report["pipeline"]["eventStates"],
            "evidence": report["pipeline"]["evidence"],
        },
        "pluginDiagnostics": {
            "count": report["pluginDiagnostics"]["count"],
            "exceptionCount": report["pluginDiagnostics"]["exceptionCount"],
        },
    }
    return {
        "dryRun": True,
        "status": "prompt_capture_completed",
        "comment": (
            "Prompt dry run completed without calling the review LLM. "
            f"Artifact: {artifact_path}"
        ),
        "issues": [],
        "promptArtifact": artifact,
    }

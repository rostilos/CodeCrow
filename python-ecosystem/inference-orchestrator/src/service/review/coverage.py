"""Execution-local state machine for candidate coverage anchors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from model.coverage import (
    CoverageAnchorV1,
    CoverageDispositionV1,
    CoverageLedgerV1,
    CoverageReceiptV1,
)


class CoverageTransitionError(RuntimeError):
    """Raised when work attempts to replace immutable coverage truth."""


@dataclass
class _DispositionState:
    anchor: CoverageAnchorV1
    state: Optional[str] = None
    reason_code: Optional[str] = None


class ExecutionCoverageTracker:
    """Map exact ledger anchor IDs to one immutable terminal disposition."""

    def __init__(self, ledger: CoverageLedgerV1):
        self.ledger = ledger
        self._states = {
            anchor.anchorId: _DispositionState(anchor=anchor)
            for anchor in ledger.anchors
        }
        self._batch_expected: dict[str, int] = {}
        self._batch_seen: dict[str, int] = {}
        self._batch_failed: dict[str, str] = {}
        for anchor in ledger.anchors:
            if anchor.initialState != "PENDING":
                self._transition(
                    [anchor.anchorId],
                    state=anchor.initialState,
                    reason_code=anchor.reasonCode,
                )
            elif not anchor.mandatory:
                self.mark_unsupported(
                    [anchor.anchorId],
                    reason_code=anchor.reasonCode or "policy_excluded",
                )
            elif anchor.kind != "TEXT_HUNK":
                self.mark_unsupported(
                    [anchor.anchorId],
                    reason_code=anchor.reasonCode or "unsupported_anchor_kind",
                )

    @property
    def total(self) -> int:
        return len(self._states)

    @property
    def mandatory_total(self) -> int:
        return sum(current.anchor.mandatory for current in self._states.values())

    @property
    def open_mandatory_total(self) -> int:
        return sum(
            current.anchor.mandatory and current.state is None
            for current in self._states.values()
        )

    def mark_examined(self, anchor_ids: Iterable[str]) -> None:
        self._transition(anchor_ids, state="EXAMINED", reason_code=None)

    def mark_unsupported(
        self,
        anchor_ids: Iterable[str],
        *,
        reason_code: str,
    ) -> None:
        self._transition(
            anchor_ids,
            state="UNSUPPORTED",
            reason_code=reason_code,
        )

    def mark_failed(
        self,
        anchor_ids: Iterable[str],
        *,
        reason_code: str,
    ) -> None:
        self._transition(anchor_ids, state="FAILED", reason_code=reason_code)

    def mark_batch_examined(self, anchor_ids: Iterable[str]) -> None:
        """Record one successful Stage 1 batch without completing shared anchors early."""

        self._record_batch_outcome(anchor_ids, failed_reason_code=None)

    def mark_batch_failed(
        self,
        anchor_ids: Iterable[str],
        *,
        reason_code: str,
    ) -> None:
        """Record one failed Stage 1 batch; failure wins after all segments finish."""

        self._record_batch_outcome(
            anchor_ids,
            failed_reason_code=reason_code,
        )

    def _record_batch_outcome(
        self,
        anchor_ids: Iterable[str],
        *,
        failed_reason_code: Optional[str],
    ) -> None:
        for anchor_id in dict.fromkeys(anchor_ids):
            current = self._states.get(anchor_id)
            if current is None:
                raise CoverageTransitionError(
                    f"unknown coverage anchorId: {anchor_id}"
                )

            expected = self._batch_expected.get(anchor_id)
            if expected is None:
                raise CoverageTransitionError(
                    f"coverage anchor {anchor_id} is not bound to a Stage 1 batch"
                )

            seen = self._batch_seen.get(anchor_id, 0)
            if seen >= expected:
                raise CoverageTransitionError(
                    f"coverage anchor {anchor_id} received more than {expected} "
                    "Stage 1 batch outcomes"
                )

            if failed_reason_code is not None:
                self._batch_failed.setdefault(anchor_id, failed_reason_code)

            seen += 1
            self._batch_seen[anchor_id] = seen
            if seen != expected:
                continue

            failure_reason = self._batch_failed.get(anchor_id)
            if failure_reason is not None:
                self.mark_failed([anchor_id], reason_code=failure_reason)
            else:
                self.mark_examined([anchor_id])

    def _transition(
        self,
        anchor_ids: Iterable[str],
        *,
        state: str,
        reason_code: Optional[str],
    ) -> None:
        for anchor_id in dict.fromkeys(anchor_ids):
            current = self._states.get(anchor_id)
            if current is None:
                raise CoverageTransitionError(f"unknown coverage anchorId: {anchor_id}")
            if current.state is None:
                current.state = state
                current.reason_code = reason_code
                continue
            if current.state == state and current.reason_code == reason_code:
                continue
            raise CoverageTransitionError(
                f"coverage anchor {anchor_id} already has terminal state "
                f"{current.state}"
            )

    def bind_batches(self, batches: list[list[dict[str, Any]]]) -> None:
        """Attach pending text anchors to every Stage 1 batch that reviews them."""

        self._batch_expected.clear()
        self._batch_seen.clear()
        self._batch_failed.clear()
        for batch in batches:
            batch_anchor_ids: set[str] = set()
            for item in batch:
                supplied = item.get("_coverage_anchor_ids") or []
                normalized: list[str] = []
                for anchor_id in supplied:
                    current = self._states.get(anchor_id)
                    if current is None:
                        raise CoverageTransitionError(
                            f"batch references foreign coverage anchorId: {anchor_id}"
                        )
                    if current.state is None and current.anchor.kind == "TEXT_HUNK":
                        normalized.append(anchor_id)

                file_info = item.get("file")
                path = getattr(file_info, "path", None)
                if path:
                    for anchor_id, current in self._states.items():
                        anchor = current.anchor
                        if (
                            current.state is None
                            and anchor.kind == "TEXT_HUNK"
                            and path in {anchor.oldPath, anchor.newPath}
                        ):
                            normalized.append(anchor_id)
                item["_coverage_anchor_ids"] = sorted(set(normalized))
                batch_anchor_ids.update(item["_coverage_anchor_ids"])

            for anchor_id in batch_anchor_ids:
                self._batch_expected[anchor_id] = (
                    self._batch_expected.get(anchor_id, 0) + 1
                )

    def finalize(self) -> CoverageReceiptV1:
        """Return every anchor exactly once, failing closed on unclaimed work."""

        for anchor_id, current in self._states.items():
            if current.state is None:
                self.mark_failed(
                    [anchor_id],
                    reason_code="coverage_not_examined",
                )

        dispositions = [
            CoverageDispositionV1(
                anchorId=anchor_id,
                state=current.state,
                reasonCode=current.reason_code,
            )
            for anchor_id, current in sorted(self._states.items())
        ]
        examined = sum(item.state == "EXAMINED" for item in dispositions)
        pending = sum(item.state == "PENDING" for item in dispositions)
        owner_pending = sum(item.state == "OWNER_PENDING" for item in dispositions)
        incomplete = sum(item.state == "INCOMPLETE" for item in dispositions)
        unsupported = sum(item.state == "UNSUPPORTED" for item in dispositions)
        failed = sum(item.state == "FAILED" for item in dispositions)
        policy_excluded = sum(item.state == "POLICY_EXCLUDED" for item in dispositions)
        deleted_recorded = sum(item.state == "DELETED_RECORDED" for item in dispositions)
        mandatory_states = [
            current.state
            for current in self._states.values()
            if current.anchor.mandatory
        ]

        if not mandatory_states:
            analysis_state = "EMPTY"
        elif all(state == "EXAMINED" for state in mandatory_states):
            analysis_state = "COMPLETE"
        elif (
            "EXAMINED" not in mandatory_states
            and "FAILED" in mandatory_states
        ):
            analysis_state = "FAILED"
        else:
            analysis_state = "PARTIAL"

        return CoverageReceiptV1(
            schemaVersion=self.ledger.schemaVersion,
            executionId=self.ledger.executionId,
            artifactManifestDigest=self.ledger.artifactManifestDigest,
            diffDigest=self.ledger.diffDigest,
            diffByteLength=self.ledger.diffByteLength,
            ledgerDigest=self.ledger.ledgerDigest,
            analysisState=analysis_state,
            total=self.total,
            examined=examined,
            unsupported=unsupported,
            failed=failed,
            incomplete=incomplete,
            pending=pending,
            ownerPending=owner_pending,
            policyExcluded=policy_excluded,
            deletedRecorded=deleted_recorded,
            dispositions=dispositions,
        )

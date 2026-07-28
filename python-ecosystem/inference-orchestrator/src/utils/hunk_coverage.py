"""Host-owned coverage accounting for immutable diff hunks."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional

from utils.diff_processor import HunkDisposition, ProcessedDiff
from utils.path_identity import normalize_repository_path


class HunkCoverageState(str, Enum):
    INGESTED = "ingested"
    PLANNED = "planned"
    REVIEWED = "reviewed"
    VALIDATED = "validated"
    COMPLETED = "completed"
    EXCLUDED = "excluded"


class ReviewManifestPreconditionError(RuntimeError):
    """Raised when acquired diff evidence cannot prove the requested scope."""


def validate_acquired_diff_manifest(
    changed_files: Iterable[str],
    deleted_files: Iterable[str],
    processed_diff: Optional[ProcessedDiff],
) -> None:
    """Require exact path coverage and parseable ownership before review."""
    if processed_diff is None:
        return

    expected_paths = [
        normalize_repository_path(path)
        for path in (*tuple(changed_files), *tuple(deleted_files))
        if normalize_repository_path(path)
    ]
    actual_paths = [
        normalize_repository_path(diff_file.path)
        for diff_file in processed_diff.files
        if normalize_repository_path(diff_file.path)
    ]

    duplicate_expected = sorted(
        path for path, count in Counter(expected_paths).items() if count > 1
    )
    duplicate_actual = sorted(
        path for path, count in Counter(actual_paths).items() if count > 1
    )
    if duplicate_expected or duplicate_actual:
        details = []
        if duplicate_expected:
            details.append(
                "duplicate requested paths: " + ", ".join(duplicate_expected)
            )
        if duplicate_actual:
            details.append(
                "duplicate parsed paths: " + ", ".join(duplicate_actual)
            )
        raise ReviewManifestPreconditionError(
            "Review diff manifest precondition failed: "
            + "; ".join(details)
            + ". No review-model stage was started."
        )

    missing = sorted(set(expected_paths) - set(actual_paths))
    unexpected = sorted(set(actual_paths) - set(expected_paths))
    if missing or unexpected:
        details = []
        if missing:
            details.append("missing paths: " + ", ".join(missing))
        if unexpected:
            details.append("unexpected paths: " + ", ".join(unexpected))
        raise ReviewManifestPreconditionError(
            "Review diff manifest precondition failed: "
            + "; ".join(details)
            + ". No review-model stage was started."
        )

    malformed_hunks = sorted(
        f"{hunk.path}:{hunk.header}"
        for hunk in processed_diff.hunk_manifest()
        if hunk.disposition is HunkDisposition.MALFORMED
    )
    if malformed_hunks:
        raise ReviewManifestPreconditionError(
            "Review diff manifest precondition failed: malformed unified-diff "
            "hunks: "
            + ", ".join(malformed_hunks)
            + ". No review-model stage was started."
        )

    unowned_text_changes = sorted(
        diff_file.path
        for diff_file in processed_diff.files
        if (
            not diff_file.is_skipped
            and not diff_file.hunks
            and (diff_file.additions or diff_file.deletions)
        )
    )
    if unowned_text_changes:
        raise ReviewManifestPreconditionError(
            "Review diff manifest precondition failed: changed text has no "
            "valid hunk owner for "
            + ", ".join(unowned_text_changes)
            + ". No review-model stage was started."
        )


@dataclass
class HunkCoverageRecord:
    hunk_id: str
    path: str
    disposition: HunkDisposition
    state: HunkCoverageState
    reason: Optional[str] = None


class HunkCoverageLedger:
    """Monotonic coverage state for every hunk in the acquired snapshot."""

    def __init__(self, records: Iterable[HunkCoverageRecord] = ()) -> None:
        records = list(records)
        if len({record.hunk_id for record in records}) != len(records):
            raise ValueError("hunk manifest contains duplicate identities")
        self._records = {record.hunk_id: record for record in records}

    @classmethod
    def from_processed_diff(cls, processed_diff: Optional[ProcessedDiff]) -> "HunkCoverageLedger":
        if processed_diff is None:
            return cls()
        records = []
        for hunk in processed_diff.hunk_manifest():
            if hunk.disposition is HunkDisposition.REVIEWABLE:
                state = HunkCoverageState.INGESTED
                reason = None
            else:
                state = HunkCoverageState.EXCLUDED
                reason = f"hunk disposition: {hunk.disposition.value}"
            records.append(HunkCoverageRecord(
                hunk_id=hunk.id,
                path=hunk.path,
                disposition=hunk.disposition,
                state=state,
                reason=reason,
            ))
        return cls(records)

    @property
    def reviewable_paths(self) -> tuple[str, ...]:
        return tuple(sorted({
            record.path
            for record in self._records.values()
            if record.disposition is HunkDisposition.REVIEWABLE
        }))

    @property
    def reviewable_hunk_ids(self) -> tuple[str, ...]:
        return tuple(sorted(
            record.hunk_id
            for record in self._records.values()
            if record.disposition is HunkDisposition.REVIEWABLE
        ))

    @property
    def reviewable_hunks(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(
            (record.hunk_id, record.path)
            for record in self._records.values()
            if record.disposition is HunkDisposition.REVIEWABLE
        ))

    def mark_planned(self, paths: Iterable[str]) -> None:
        planned = set(paths)
        missing = sorted(set(self.reviewable_paths) - planned)
        if missing:
            raise RuntimeError("Review plan omitted reviewable hunk paths: " + ", ".join(missing))
        self._advance_paths(planned, HunkCoverageState.INGESTED, HunkCoverageState.PLANNED)

    def mark_reviewed(self, paths: Iterable[str]) -> None:
        reviewed = set(paths)
        missing = sorted(set(self.reviewable_paths) - reviewed)
        if missing:
            raise RuntimeError("Stage 1 omitted reviewable hunk paths: " + ", ".join(missing))
        self._advance_paths(reviewed, HunkCoverageState.PLANNED, HunkCoverageState.REVIEWED)

    def mark_reviewed_hunks(self, hunk_ids: Iterable[str]) -> None:
        reviewed = set(hunk_ids)
        expected = set(self.reviewable_hunk_ids)
        missing = sorted(expected - reviewed)
        if missing:
            raise RuntimeError(
                "Stage 1 omitted reviewable hunk identities: " + ", ".join(missing)
            )
        unexpected = sorted(reviewed - expected)
        if unexpected:
            raise RuntimeError(
                "Stage 1 reported unknown reviewable hunk identities: "
                + ", ".join(unexpected)
            )
        self._advance_hunks(
            reviewed,
            HunkCoverageState.PLANNED,
            HunkCoverageState.REVIEWED,
        )

    def mark_validated(self) -> None:
        self._advance_all(HunkCoverageState.REVIEWED, HunkCoverageState.VALIDATED)

    def complete(self) -> None:
        self._advance_all(HunkCoverageState.VALIDATED, HunkCoverageState.COMPLETED)

    def assert_complete(self) -> None:
        incomplete = sorted(
            f"{record.path}:{record.hunk_id}:{record.state.value}"
            for record in self._records.values()
            if record.state not in {HunkCoverageState.COMPLETED, HunkCoverageState.EXCLUDED}
        )
        if incomplete:
            raise RuntimeError("Review hunk coverage is incomplete: " + ", ".join(incomplete))

    def summary(self) -> dict[str, int]:
        return {
            state.value: sum(record.state is state for record in self._records.values())
            for state in HunkCoverageState
        }

    def _advance_paths(
        self,
        paths: set[str],
        expected: HunkCoverageState,
        target: HunkCoverageState,
    ) -> None:
        for record in self._records.values():
            if record.path not in paths or record.disposition is not HunkDisposition.REVIEWABLE:
                continue
            if record.state is not expected:
                raise RuntimeError(
                    f"Invalid hunk coverage transition for {record.hunk_id}: "
                    f"{record.state.value} -> {target.value}"
                )
            record.state = target

    def _advance_all(self, expected: HunkCoverageState, target: HunkCoverageState) -> None:
        for record in self._records.values():
            if record.disposition is not HunkDisposition.REVIEWABLE:
                continue
            if record.state is not expected:
                raise RuntimeError(
                    f"Invalid hunk coverage transition for {record.hunk_id}: "
                    f"{record.state.value} -> {target.value}"
                )
            record.state = target

    def _advance_hunks(
        self,
        hunk_ids: set[str],
        expected: HunkCoverageState,
        target: HunkCoverageState,
    ) -> None:
        for hunk_id in hunk_ids:
            record = self._records[hunk_id]
            if record.disposition is not HunkDisposition.REVIEWABLE:
                continue
            if record.state is not expected:
                raise RuntimeError(
                    f"Invalid hunk coverage transition for {record.hunk_id}: "
                    f"{record.state.value} -> {target.value}"
                )
            record.state = target

"""Bounded, language-neutral specialist selection over already-owned review units."""
from dataclasses import dataclass
import os
import re
from typing import Any


@dataclass(frozen=True)
class ReviewSpecialist:
    name: str
    instructions: str
    cues: tuple[str, ...]


SPECIALISTS = (
    ReviewSpecialist("security", "Trace untrusted input, authorization and tenant boundaries. Seek a concrete bypass or exposure and the existing guard that could disprove it. Ignore style and generic hardening.",
                     ("security", "authoriz", "authentic", "permission", "tenant", "credential", "sanitize")),
    ReviewSpecialist("state_integrity", "Trace writes, deletes and transaction boundaries. Check partial failure, lost or duplicated updates, rollback and durable state. Report a concrete inconsistent outcome, and test the existing recovery path against that hypothesis.",
                     ("transaction", "rollback", "persist", "delete", "commit", "state_integrity", "database")),
    ReviewSpecialist("concurrency", "Trace ordering, cancellation, retry and resource ownership across concurrent operations. Construct an explicit harmful interleaving and check synchronization/recovery that could disprove it.",
                     ("concurr", "race", "lock", "semaphore", "cancel", "retry", "async", "deadlock")),
)


def specialist_limit() -> int:
    if os.getenv("REVIEW_SPECIALISTS_ENABLED", "true").strip().lower() in {"false", "0", "no", "off"}:
        return 0
    try:
        return max(0, min(3, int(os.getenv("REVIEW_SPECIALIST_MAX_PASSES", "2"))))
    except ValueError:
        return 2


def select_specialist_batches(batches: list, prepared_context: Any, cap: int) -> list[tuple[ReviewSpecialist, list]]:
    """At most one supplemental pass per category; core discovery is already complete."""
    ranked = []
    for specialist in SPECIALISTS:
        choices = []
        for index, batch in enumerate(batches):
            fragments = []
            risk = 0
            for item in batch:
                file = item["file"]
                fragments.extend(str(area) for area in getattr(file, "focus_areas", ()))
                diff = prepared_context.diff_by_path.get(file.path)
                fragments.append(str(getattr(diff, "content", "")))
                risk += int(str(getattr(file, "risk_level", "")).upper() in {"HIGH", "CRITICAL"})
            text = "\n".join(fragments).casefold()
            matches = sum(bool(re.search(r"\b" + re.escape(cue), text)) for cue in specialist.cues)
            if matches:
                choices.append((matches * 10 + risk, -index, batch))
        if choices:
            score, index, batch = max(choices, key=lambda choice: choice[:2])
            ranked.append((score, specialist.name, specialist, batch))
    return [(specialist, batch) for _, _, specialist, batch in sorted(ranked, key=lambda item: (-item[0], item[1]))[:min(cap, specialist_limit())]]

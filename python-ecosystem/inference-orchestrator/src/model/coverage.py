"""Strict wire models for the candidate hunk-coverage contract."""

from __future__ import annotations

import json
from hashlib import sha256
from hmac import compare_digest
from typing import Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


_SHA_256 = r"^[0-9a-f]{64}$"
_EXECUTION_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$"
_REASON_CODE = r"^[a-z0-9][a-z0-9_.:-]{0,127}$"
_JAVA_LONG_MAX = 9_223_372_036_854_775_807


def _canonical_sha256(document: dict[str, object]) -> str:
    encoded = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


class CoverageAnchorV1(BaseModel):
    """One immutable unit of diff work selected by the Java producer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    anchorId: StrictStr = Field(pattern=_SHA_256)
    executionId: StrictStr = Field(pattern=_EXECUTION_IDENTIFIER)
    parentHunkId: StrictStr = Field(pattern=_SHA_256)
    changeId: StrictStr = Field(pattern=_SHA_256)
    kind: Literal["TEXT_HUNK", "FILE_CHANGE"]
    oldPath: Optional[StrictStr]
    newPath: Optional[StrictStr]
    oldStart: StrictInt = Field(ge=0, le=_JAVA_LONG_MAX)
    oldLineCount: StrictInt = Field(ge=0, le=_JAVA_LONG_MAX)
    newStart: StrictInt = Field(ge=0, le=_JAVA_LONG_MAX)
    newLineCount: StrictInt = Field(ge=0, le=_JAVA_LONG_MAX)
    changeStatus: Literal["ADD", "MODIFY", "DELETE", "RENAME", "COPY"]
    sourceArtifactId: StrictStr = Field(pattern=_EXECUTION_IDENTIFIER)
    sourceDigest: StrictStr = Field(pattern=_SHA_256)
    mandatory: StrictBool
    initialState: Literal[
        "PENDING",
        "OWNER_PENDING",
        "EXAMINED",
        "INCOMPLETE",
        "UNSUPPORTED",
        "FAILED",
        "POLICY_EXCLUDED",
        "DELETED_RECORDED",
    ]
    reasonCode: Optional[StrictStr] = Field(default=None, pattern=_REASON_CODE)

    @field_validator("oldPath", "newPath")
    @classmethod
    def validate_path(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and (not value.strip() or "\x00" in value):
            raise ValueError("coverage anchor path is invalid")
        return value

    @model_validator(mode="after")
    def validate_state_and_coordinates(self) -> "CoverageAnchorV1":
        if self.oldPath is None and self.newPath is None:
            raise ValueError("coverage anchor requires an oldPath or newPath")
        if self.initialState == "PENDING" and self.reasonCode is not None:
            raise ValueError("PENDING coverage anchor cannot have a reasonCode")
        if self.initialState not in {"PENDING", "EXAMINED"} and self.reasonCode is None:
            raise ValueError(f"{self.initialState} coverage anchor requires a reasonCode")
        return self


class CoverageLedgerV1(BaseModel):
    """Self-verifying immutable work ledger transported in queue v2."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: StrictInt = Field(ge=1, le=1)
    executionId: StrictStr = Field(pattern=_EXECUTION_IDENTIFIER)
    artifactManifestDigest: StrictStr = Field(pattern=_SHA_256)
    diffDigest: StrictStr = Field(pattern=_SHA_256)
    diffByteLength: StrictInt = Field(ge=0, le=_JAVA_LONG_MAX)
    anchorCount: StrictInt = Field(ge=0, le=_JAVA_LONG_MAX)
    anchors: list[CoverageAnchorV1]
    ledgerDigest: StrictStr = Field(pattern=_SHA_256)

    @field_validator("anchors", mode="before")
    @classmethod
    def require_canonical_anchor_order(cls, value: object) -> object:
        if not isinstance(value, (list, tuple)):
            return value
        anchor_ids = [
            (
                anchor.get("anchorId", "")
                if isinstance(anchor, dict)
                else getattr(anchor, "anchorId", "")
            )
            for anchor in value
        ]
        if anchor_ids != sorted(anchor_ids):
            raise ValueError("coverage anchors must use canonical anchorId order")
        return value

    @model_validator(mode="after")
    def verify_ledger(self) -> "CoverageLedgerV1":
        if self.anchorCount != len(self.anchors):
            raise ValueError("anchorCount declares a missing coverage anchor")

        anchor_ids = [anchor.anchorId for anchor in self.anchors]
        if len(set(anchor_ids)) != len(anchor_ids):
            raise ValueError("coverage ledger contains a duplicate anchorId")

        for anchor in self.anchors:
            if anchor.executionId != self.executionId:
                raise ValueError("coverage anchor executionId is foreign")
            if not compare_digest(anchor.sourceDigest, self.diffDigest):
                raise ValueError("coverage anchor sourceDigest conflicts with diffDigest")

        coordinates = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"ledgerDigest"},
        )
        expected = _canonical_sha256(coordinates)
        if not compare_digest(expected, self.ledgerDigest):
            raise ValueError("ledgerDigest does not match canonical coverage ledger")
        return self


class CoverageDispositionV1(BaseModel):
    """Terminal producer disposition for one exact anchor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    anchorId: StrictStr = Field(pattern=_SHA_256)
    state: Literal[
        "PENDING",
        "OWNER_PENDING",
        "EXAMINED",
        "INCOMPLETE",
        "UNSUPPORTED",
        "FAILED",
        "POLICY_EXCLUDED",
        "DELETED_RECORDED",
    ]
    reasonCode: Optional[StrictStr] = Field(default=None, pattern=_REASON_CODE)

    @model_validator(mode="after")
    def validate_reason(self) -> "CoverageDispositionV1":
        if self.state == "EXAMINED" and self.reasonCode is not None:
            raise ValueError("EXAMINED disposition cannot have a reasonCode")
        if self.state not in {"PENDING", "EXAMINED"} and self.reasonCode is None:
            raise ValueError(f"{self.state} disposition requires a reasonCode")
        return self


class CoverageReceiptV1(BaseModel):
    """Complete execution-local result returned to the durable Java ledger."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: StrictInt = Field(ge=1, le=1)
    executionId: StrictStr = Field(pattern=_EXECUTION_IDENTIFIER)
    artifactManifestDigest: StrictStr = Field(pattern=_SHA_256)
    diffDigest: StrictStr = Field(pattern=_SHA_256)
    diffByteLength: StrictInt = Field(ge=0, le=_JAVA_LONG_MAX)
    ledgerDigest: StrictStr = Field(pattern=_SHA_256)
    analysisState: Literal["EMPTY", "PARTIAL", "FAILED", "COMPLETE"]
    total: StrictInt = Field(ge=0, le=_JAVA_LONG_MAX)
    examined: StrictInt = Field(ge=0, le=_JAVA_LONG_MAX)
    unsupported: StrictInt = Field(ge=0, le=_JAVA_LONG_MAX)
    failed: StrictInt = Field(ge=0, le=_JAVA_LONG_MAX)
    incomplete: StrictInt = Field(ge=0, le=_JAVA_LONG_MAX)
    pending: StrictInt = Field(ge=0, le=_JAVA_LONG_MAX)
    ownerPending: StrictInt = Field(ge=0, le=_JAVA_LONG_MAX)
    policyExcluded: StrictInt = Field(ge=0, le=_JAVA_LONG_MAX)
    deletedRecorded: StrictInt = Field(ge=0, le=_JAVA_LONG_MAX)
    dispositions: list[CoverageDispositionV1]

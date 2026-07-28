from typing import Dict, List

from pydantic import BaseModel, Field


class ProjectCapabilitiesDto(BaseModel):
    """Internal transport for host-selected project capabilities."""

    repositoryPlugins: List[str] = Field(default_factory=list)
    filePlugins: Dict[str, List[str]] = Field(default_factory=dict)
    detectionEvidence: Dict[str, List[str]] = Field(default_factory=dict)
    unavailableCapabilities: List[str] = Field(default_factory=list)
    fingerprint: str
    descriptorFingerprint: str

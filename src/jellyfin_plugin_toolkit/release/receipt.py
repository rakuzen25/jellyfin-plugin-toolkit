"""The durable public release contract, independent of CI reuse policy."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolkitProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    distribution: Literal["jellyfin-plugin-toolkit"]
    version: str
    source: str
    sha256: str
    python_lock_sha256: str


class PublicReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[7] = Field(default=7, alias="schema")
    repository: str
    commit: str
    run_id: str
    run_attempt: int = Field(ge=1)
    run_head_sha: str
    release: Literal[True]
    groups: dict[str, Literal["success"]]
    artifacts: dict[str, str]
    toolkit: ToolkitProvenance
    validated_at: str

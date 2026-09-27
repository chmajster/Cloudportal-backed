from pydantic import BaseModel, Field

from app.api.schemas import BlueprintInput, HostnameSchemeInput, Input


class BlueprintBundleInput(Input):
    blueprint: BlueprintInput
    hostname_scheme: HostnameSchemeInput | None = None


class BlueprintDeleteOutput(BaseModel):
    deleted: bool
    queued: bool
    job_id: str | None = None
    blocking_job_ids: list[str] = Field(default_factory=list)

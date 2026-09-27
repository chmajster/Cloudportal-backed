from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


PlanId = Annotated[str, Field(min_length=36, max_length=36, pattern=r'^[0-9a-fA-F-]{36}$')]


class AvailabilityPlanInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: Annotated[str, Field(min_length=1, max_length=100)]
    description: Annotated[str, Field(max_length=2000)] = ''
    state: Literal['started', 'stopped', 'ignored', 'disabled'] = 'started'
    group: Annotated[str | None, Field(max_length=63, pattern=r'^[A-Za-z0-9_.-]+$')] = None
    max_restart: int = Field(default=1, ge=0, le=100)
    max_relocate: int = Field(default=1, ge=0, le=100)
    is_active: bool = True


class AvailabilityAssignmentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    plan_id: PlanId

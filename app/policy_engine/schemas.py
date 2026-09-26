from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.policy_engine.engine import ENFORCEMENTS, POLICY_TYPES, STATUSES, validate_condition_tree, validate_effects


PolicyStatus = Literal["draft", "dry_run", "enforced", "disabled", "archived"]
PolicyEnforcement = Literal["hard", "soft", "advisory"]
PolicyScopeLevel = Literal["global", "tenant", "project"]


class PolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=8000)
    policy_type: str = Field(default="deployment", min_length=1, max_length=40)
    priority: int = Field(default=5000, ge=0, le=10000)
    enforcement: PolicyEnforcement = "hard"
    status: PolicyStatus = "draft"
    scope_level: PolicyScopeLevel = "project"
    scope: dict[str, Any] = Field(default_factory=dict)
    condition: dict[str, Any] = Field(default_factory=dict)
    effects: list[dict[str, Any]] = Field(min_length=1)

    @field_validator("policy_type")
    @classmethod
    def known_type(cls, value):
        if value not in POLICY_TYPES:
            raise ValueError("Unsupported policy type")
        return value

    @field_validator("condition")
    @classmethod
    def valid_condition(cls, value):
        validate_condition_tree(value)
        return value

    @field_validator("scope")
    @classmethod
    def valid_scope(cls, value):
        if len(value) > 32:
            raise ValueError("Scope contains too many dimensions")
        if value.get("conditions"):
            validate_condition_tree(value["conditions"])
        return value

    @field_validator("effects")
    @classmethod
    def valid_effects(cls, value):
        validate_effects(value)
        return value


class PolicyUpdate(PolicyInput):
    expected_version: int = Field(ge=1)


class PolicyRollback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    expected_version: int = Field(ge=1)


class PolicyExceptionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=8000)
    ticket: str = Field(default="", max_length=160)
    status: Literal["pending", "approved", "revoked"] = "approved"
    condition: dict[str, Any] = Field(default_factory=dict)
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    @field_validator("condition")
    @classmethod
    def valid_condition(cls, value):
        validate_condition_tree(value)
        return value


class EvaluationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, max_length=100)
    resource_type: str = Field(default="resource", min_length=1, max_length=64)
    resource_id: str | None = Field(default=None, max_length=160)
    context: dict[str, Any] = Field(default_factory=dict)


class PreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contexts: list[EvaluationInput] = Field(min_length=1, max_length=200)


class DecisionFilter(BaseModel):
    action: str | None = None
    decision: Literal["allow", "deny", "approval_required"] | None = None

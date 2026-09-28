"""HTTP contracts for administrator-managed Event Broker subscriptions."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.events.filters import validate_filter


Phase = Literal["PRE", "POST"]
ScopeLevel = Literal["project", "tenant", "global"]


class EventSubscriptionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=8000)
    event_pattern: str = Field(min_length=2, max_length=128)
    phase: Phase = "POST"
    priority: int = Field(default=5000, ge=0, le=10000)
    enabled: bool = True
    blocking: bool = False
    filter: dict[str, Any] = Field(default_factory=dict)
    action_type: str = Field(min_length=1, max_length=48)
    action_config: dict[str, Any] = Field(default_factory=dict)
    secret_config: dict[str, Any] | None = None
    credential_id: int | None = Field(default=None, gt=0)
    run_as_token_id: int | None = Field(default=None, gt=0)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    max_attempts: int = Field(default=3, ge=1, le=20)
    retry_delay_seconds: int = Field(default=10, ge=0, le=3600)
    backoff: Literal["fixed", "exponential"] = "exponential"
    max_retry_delay_seconds: int = Field(default=300, ge=0, le=86400)
    retry_on: list[Literal["timeout", "http_429", "http_5xx", "connection", "worker_error"]] = Field(
        default_factory=lambda: ["timeout", "http_429", "http_5xx", "connection", "worker_error"]
    )
    fail_policy: Literal["open", "closed"] = "open"
    execution_policy: Literal["sequential", "parallel", "first_match", "all"] = "all"
    stop_on_block: bool = True
    stop_on_failure: bool = False
    continue_on_failure: bool = True
    concurrency_limit: int = Field(default=5, ge=1, le=100)
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=100000)
    rate_limit_policy: Literal["queue", "reject"] = "queue"
    dedup_window_seconds: int = Field(default=0, ge=0, le=86400)
    scope_level: ScopeLevel = "project"

    @field_validator("filter")
    @classmethod
    def valid_filter(cls, value):
        validate_filter(value)
        return value


class EventSubscriptionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=8000)
    event_pattern: str | None = Field(default=None, min_length=2, max_length=128)
    phase: Phase | None = None
    priority: int | None = Field(default=None, ge=0, le=10000)
    enabled: bool | None = None
    blocking: bool | None = None
    filter: dict[str, Any] | None = None
    action_type: str | None = Field(default=None, min_length=1, max_length=48)
    action_config: dict[str, Any] | None = None
    secret_config: dict[str, Any] | None = None
    clear_secret: bool = False
    credential_id: int | None = Field(default=None, gt=0)
    clear_credential: bool = False
    run_as_token_id: int | None = Field(default=None, gt=0)
    clear_run_as_token: bool = False
    timeout_seconds: int | None = Field(default=None, ge=1, le=120)
    max_attempts: int | None = Field(default=None, ge=1, le=20)
    retry_delay_seconds: int | None = Field(default=None, ge=0, le=3600)
    backoff: Literal["fixed", "exponential"] | None = None
    max_retry_delay_seconds: int | None = Field(default=None, ge=0, le=86400)
    retry_on: list[Literal["timeout", "http_429", "http_5xx", "connection", "worker_error"]] | None = None
    fail_policy: Literal["open", "closed"] | None = None
    execution_policy: Literal["sequential", "parallel", "first_match", "all"] | None = None
    stop_on_block: bool | None = None
    stop_on_failure: bool | None = None
    continue_on_failure: bool | None = None
    concurrency_limit: int | None = Field(default=None, ge=1, le=100)
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=100000)
    clear_rate_limit: bool = False
    rate_limit_policy: Literal["queue", "reject"] | None = None
    dedup_window_seconds: int | None = Field(default=None, ge=0, le=86400)

    @field_validator("filter")
    @classmethod
    def valid_filter(cls, value):
        if value is not None:
            validate_filter(value)
        return value


class EventSubscriptionTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subscription: EventSubscriptionCreate | None = None
    event_id: str | None = Field(default=None, max_length=64)
    event: dict[str, Any] | None = None
    execute: bool = False


class EventReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subscription_id: str | None = Field(default=None, max_length=64)
    reason: str = Field(default="", max_length=500)


class EventBrokerSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_private_networks: bool = False
    allowed_domains: list[str] = Field(default_factory=list, max_length=500)
    blocked_hosts: list[str] = Field(default_factory=list, max_length=500)
    blocked_cidrs: list[str] = Field(default_factory=list, max_length=500)
    max_chain_depth: int = Field(default=16, ge=1, le=64)
    response_body_limit: int = Field(default=65536, ge=1024, le=1048576)
    events_retention_days: int = Field(default=30, ge=1, le=3650)
    deliveries_retention_days: int = Field(default=90, ge=1, le=3650)
    dlq_retention_days: int = Field(default=180, ge=1, le=3650)


class DeadLetterAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="", max_length=500)

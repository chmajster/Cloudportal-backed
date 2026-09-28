"""Add production Event Broker subscription runtime.

Revision ID: e7b4a9c218d5
Revises: c13d9a42b5e7
"""
from alembic import op
import sqlalchemy as sa


revision = "e7b4a9c218d5"
down_revision = "c13d9a42b5e7"
branch_labels = None
depends_on = None


def upgrade():
    bigint = sa.BigInteger().with_variant(sa.Integer(), "sqlite")

    op.create_table(
        "event_contexts",
        sa.Column("event_sequence", bigint, sa.ForeignKey("event_records.sequence", ondelete="CASCADE"), primary_key=True),
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36)),
        sa.Column("project_id", sa.String(36)),
        sa.Column("apmid", sa.String(64)),
        sa.Column("environment", sa.String(32)),
        sa.Column("resource_id", sa.String(255)),
        sa.Column("resource_type", sa.String(64)),
        sa.Column("deployment_id", sa.String(36)),
        sa.Column("provider_id", sa.String(64)),
        sa.Column("provider_type", sa.String(32)),
        sa.Column("resource_pool_id", sa.String(64)),
        sa.Column("blueprint_id", sa.String(64)),
        sa.Column("job_id", sa.String(36)),
        sa.Column("workflow_id", sa.String(64)),
        sa.Column("parent_event_id", sa.String(36)),
        sa.Column("root_event_id", sa.String(36)),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
    )
    for column in (
        "event_id", "tenant_id", "project_id", "apmid", "environment", "resource_id",
        "resource_type", "deployment_id", "provider_id", "provider_type", "resource_pool_id",
        "blueprint_id", "job_id", "workflow_id", "parent_event_id", "root_event_id",
    ):
        op.create_index(f"ix_event_contexts_{column}", "event_contexts", [column])
    op.create_index("ix_event_context_scope_time", "event_contexts", ["tenant_id", "project_id", "event_sequence"])
    op.create_index("ix_event_context_resource", "event_contexts", ["resource_id", "event_sequence"])
    op.create_index("ix_event_context_job", "event_contexts", ["job_id", "event_sequence"])
    op.create_index("ix_event_context_workflow", "event_contexts", ["workflow_id", "event_sequence"])
    op.create_unique_constraint("uq_event_context_event_id", "event_contexts", ["event_id"])

    op.create_table(
        "event_subscriptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("event_pattern", sa.String(128), nullable=False),
        sa.Column("phase", sa.String(8), nullable=False, server_default="POST"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="5000"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_blocking", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("filter_json", sa.JSON(), nullable=False),
        sa.Column("action_type", sa.String(48), nullable=False),
        sa.Column("action_config", sa.JSON(), nullable=False),
        sa.Column("encrypted_action_secret", sa.LargeBinary()),
        sa.Column("credential_id", sa.Integer(), sa.ForeignKey("credentials.id", ondelete="RESTRICT")),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("retry_delay_seconds", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("backoff", sa.String(16), nullable=False, server_default="exponential"),
        sa.Column("max_retry_delay_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("retry_on", sa.JSON(), nullable=False),
        sa.Column("fail_policy", sa.String(16), nullable=False, server_default="open"),
        sa.Column("execution_policy", sa.String(16), nullable=False, server_default="all"),
        sa.Column("stop_on_block", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("stop_on_failure", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("continue_on_failure", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("concurrency_limit", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("rate_limit_per_minute", sa.Integer()),
        sa.Column("rate_limit_policy", sa.String(16), nullable=False, server_default="queue"),
        sa.Column("dedup_window_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tenant_id", sa.String(36)),
        sa.Column("project_id", sa.String(36)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for column in ("event_pattern", "phase", "priority", "is_enabled", "is_blocking", "action_type", "credential_id", "tenant_id", "project_id", "created_by"):
        op.create_index(f"ix_event_subscriptions_{column}", "event_subscriptions", [column])
    op.create_index("ix_event_subscription_match", "event_subscriptions", ["event_pattern", "phase", "is_enabled", "priority"])
    op.create_index("ix_event_subscription_scope", "event_subscriptions", ["tenant_id", "project_id", "is_enabled"])

    op.create_table(
        "event_replays",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("original_event_id", sa.String(36), nullable=False),
        sa.Column("original_event_sequence", bigint, sa.ForeignKey("event_records.sequence", ondelete="RESTRICT"), nullable=False),
        sa.Column("target_subscription_id", sa.String(36), sa.ForeignKey("event_subscriptions.id", ondelete="SET NULL")),
        sa.Column("requested_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for column in ("original_event_id", "original_event_sequence", "target_subscription_id", "requested_by", "created_at"):
        op.create_index(f"ix_event_replays_{column}", "event_replays", [column])

    op.create_table(
        "event_subscription_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_sequence", bigint, sa.ForeignKey("event_records.sequence", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("subscription_id", sa.String(36), sa.ForeignKey("event_subscriptions.id", ondelete="SET NULL")),
        sa.Column("replay_id", sa.String(36), sa.ForeignKey("event_replays.id", ondelete="SET NULL")),
        sa.Column("original_delivery_id", sa.String(36)),
        sa.Column("subscription_version", sa.Integer(), nullable=False),
        sa.Column("subscription_snapshot", sa.JSON(), nullable=False),
        sa.Column("action_type", sa.String(48), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("blocking", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("worker_id", sa.String(128)),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("response_code", sa.Integer()),
        sa.Column("response_headers", sa.JSON(), nullable=False),
        sa.Column("response_body", sa.Text()),
        sa.Column("external_job_id", sa.String(128)),
        sa.Column("decision", sa.String(24)),
        sa.Column("decision_reason", sa.String(1000)),
        sa.Column("decision_patch", sa.JSON(), nullable=False),
        sa.Column("error_type", sa.String(128)),
        sa.Column("error_message", sa.String(1000)),
        sa.Column("next_retry_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_event_subscription_delivery_idempotency"),
    )
    for column in ("event_sequence", "event_id", "subscription_id", "replay_id", "original_delivery_id", "action_type", "status", "blocking", "worker_id", "external_job_id", "next_retry_at"):
        op.create_index(f"ix_event_subscription_deliveries_{column}", "event_subscription_deliveries", [column])
    op.create_index("ix_event_delivery_due", "event_subscription_deliveries", ["status", "next_retry_at"])
    op.create_index("ix_event_delivery_subscription_status", "event_subscription_deliveries", ["subscription_id", "status"])
    op.create_index("ix_event_delivery_event_status", "event_subscription_deliveries", ["event_sequence", "status"])

    op.create_table(
        "event_delivery_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("delivery_id", sa.String(36), sa.ForeignKey("event_subscription_deliveries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("response_code", sa.Integer()),
        sa.Column("response_body", sa.Text()),
        sa.Column("error_type", sa.String(128)),
        sa.Column("error_message", sa.String(1000)),
        sa.UniqueConstraint("delivery_id", "attempt", name="uq_event_delivery_attempt"),
    )
    op.create_index("ix_event_delivery_attempts_delivery_id", "event_delivery_attempts", ["delivery_id"])
    op.create_index("ix_event_delivery_attempts_status", "event_delivery_attempts", ["status"])
    op.create_index("ix_event_attempt_delivery_time", "event_delivery_attempts", ["delivery_id", "started_at"])

    op.create_table(
        "event_dead_letters",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("delivery_id", sa.String(36), sa.ForeignKey("event_subscription_deliveries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("subscription_id", sa.String(36)),
        sa.Column("status", sa.String(24), nullable=False, server_default="OPEN"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_type", sa.String(128)),
        sa.Column("last_error_message", sa.String(1000)),
        sa.Column("last_response_code", sa.Integer()),
        sa.Column("replay_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("delivery_id", name="uq_event_dead_letter_delivery"),
    )
    for column in ("delivery_id", "event_id", "subscription_id", "status", "replay_id"):
        op.create_index(f"ix_event_dead_letters_{column}", "event_dead_letters", [column])


def downgrade():
    op.drop_table("event_dead_letters")
    op.drop_table("event_delivery_attempts")
    op.drop_table("event_subscription_deliveries")
    op.drop_table("event_replays")
    op.drop_table("event_subscriptions")
    op.drop_table("event_contexts")

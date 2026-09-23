"""Add instance backup and migration metadata.

Revision ID: 1b7d4a9c2e61
Revises: f2c7a91d4e63
"""
from alembic import op
import sqlalchemy as sa

revision = "1b7d4a9c2e61"
down_revision = "f2c7a91d4e63"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "instance_backups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("backup_uuid", sa.String(length=36), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("application_version", sa.String(length=64), nullable=True),
        sa.Column("git_commit", sa.String(length=64), nullable=True),
        sa.Column("alembic_revision", sa.String(length=64), nullable=True),
        sa.Column("install_mode", sa.String(length=16), nullable=True),
        sa.Column("secret_backend", sa.String(length=32), nullable=True),
        sa.Column("source_hostname", sa.String(length=253), nullable=True),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("token_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("backup_uuid"),
    )
    op.create_index("ix_instance_backups_backup_uuid", "instance_backups", ["backup_uuid"])
    op.create_index("ix_instance_backups_origin", "instance_backups", ["origin"])
    op.create_index("ix_instance_backups_status", "instance_backups", ["status"])
    op.create_index("ix_instance_backups_expires_at", "instance_backups", ["expires_at"])
    op.create_index("ix_instance_backups_request_id", "instance_backups", ["request_id"])
    op.create_index("ix_instance_backups_created_at", "instance_backups", ["created_at"])


def downgrade():
    op.drop_index("ix_instance_backups_created_at", table_name="instance_backups")
    op.drop_index("ix_instance_backups_request_id", table_name="instance_backups")
    op.drop_index("ix_instance_backups_expires_at", table_name="instance_backups")
    op.drop_index("ix_instance_backups_status", table_name="instance_backups")
    op.drop_index("ix_instance_backups_origin", table_name="instance_backups")
    op.drop_index("ix_instance_backups_backup_uuid", table_name="instance_backups")
    op.drop_table("instance_backups")

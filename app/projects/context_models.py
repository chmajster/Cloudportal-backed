"""Persisted project selection; deliberately not an authorization grant."""
from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class UserProjectContext(Base):
    """A server-side preference, not an authorization grant."""
    __tablename__ = 'user_project_contexts'
    __table_args__ = (
        ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'],
                             ondelete='RESTRICT', name='fk_user_project_contexts_project'),
        CheckConstraint('version >= 1', name='ck_user_project_contexts_version'),
        CheckConstraint('(tenant_id IS NULL) = (project_id IS NULL)', name='ck_user_project_contexts_pair'),
    )
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __mapper_args__ = {'version_id_col': version}

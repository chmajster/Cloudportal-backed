"""Allowlist references to shared infrastructure; secrets stay in Credential."""
from sqlalchemy import ForeignKey, ForeignKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.models import Credential, Provider


class ProjectCredentialAccess(Base):
    __tablename__ = 'project_credential_access'
    __table_args__ = (ForeignKeyConstraint(['tenant_id', 'project_id'],
        ['projects.tenant_id', 'projects.id'], name='fk_credential_access_project', ondelete='RESTRICT'),)
    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    credential_id: Mapped[int] = mapped_column(ForeignKey('credentials.id', ondelete='CASCADE'), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    credential: Mapped[Credential] = relationship()


class ProjectProviderAccess(Base):
    __tablename__ = 'project_provider_access'
    __table_args__ = (ForeignKeyConstraint(['tenant_id', 'project_id'],
        ['projects.tenant_id', 'projects.id'], name='fk_provider_access_project', ondelete='RESTRICT'),)
    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey('providers.id', ondelete='CASCADE'), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    provider: Mapped[Provider] = relationship()

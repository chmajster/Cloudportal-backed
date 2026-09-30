from sqlalchemy import Boolean, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models import Timestamp, uid


class OrganizationAPMID(Timestamp, Base):
    __tablename__ = 'organization_apmids'
    __table_args__ = (
        UniqueConstraint('organization_id', 'code', name='uq_organization_apmids_org_code'),
        Index('ix_organization_apmids_org_enabled', 'organization_id', 'enabled', 'code'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey('tenants.id', ondelete='RESTRICT'), index=True,
    )
    code: Mapped[str] = mapped_column(String(63))
    name: Mapped[str] = mapped_column(String(100), default='')
    description: Mapped[str] = mapped_column(Text, default='')
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))

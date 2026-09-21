"""The shared authorization mutation lock used by global and scoped RBAC."""
from sqlalchemy import select
from app.models import Setting


def governance_lock(db):
    return db.scalar(select(Setting).where(Setting.key == 'governance').with_for_update())

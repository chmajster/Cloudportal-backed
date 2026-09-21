"""The shared authorization mutation lock used by global and scoped RBAC."""
from sqlalchemy import select
from app.models import Setting


def governance_lock(db):
    # Authentication can leave Token.last_used_at dirty. Acquire the governance
    # row before flushing token/user writes, matching the revocation lock order.
    with db.no_autoflush:
        return db.scalar(select(Setting).where(Setting.key == 'governance').with_for_update())

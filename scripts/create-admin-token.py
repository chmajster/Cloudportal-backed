#!/usr/bin/env python3
"""Offline token recovery. Run from the application directory as cloudportal with CP_* configured."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from app.database import session
from app.models import User
from app.rbac.service import ALL_PERMISSIONS
from app.security.core import effective_permissions, issue_token

parser=argparse.ArgumentParser()
parser.add_argument('--username', required=True)
args=parser.parse_args()
with session() as db:
    user=db.scalar(select(User).where(User.username==args.username, User.is_active.is_(True), User.is_locked.is_(False)))
    if not user or not ALL_PERMISSIONS <= effective_permissions(user):
        raise SystemExit('An existing active administrator is required')
    _,token=issue_token(db,user,'Recovered administrator token',ALL_PERMISSIONS)
    db.commit()
print('Save this token now; it cannot be read again:')
print(token)

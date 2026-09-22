"""Quota concurrency acceptance requires PostgreSQL row locks."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from app.database import session
from app.projects.permissions import DEFAULT_PROJECT_ID
from app.quotas.models import ProjectQuotaUsage
from app.quotas.service import QuotaDelta, commit_reservation, reserve, set_project_limit
from app.resource_scope.authorization import Scope
from app.tenancy.permissions import DEFAULT_TENANT_ID

pytestmark = pytest.mark.skipif(
    not os.environ.get('TEST_DATABASE_URL', '').startswith('postgresql'),
    reason='PostgreSQL required for atomic quota reservation verification',
)

SCOPE = Scope(DEFAULT_TENANT_ID, DEFAULT_PROJECT_ID)


def test_ten_concurrent_requests_for_one_remaining_slot_have_exactly_one_winner(system):
    with session() as db:
        set_project_limit(db, SCOPE, 'vm_count', 10)
        baseline = reserve(
            db, QuotaDelta(SCOPE, 'deployment', 'baseline-nine', 'seed', {'vm_count': 9}),
            'quota-baseline-nine', 1,
        )
        commit_reservation(db, baseline.id)
        db.commit()

    barrier = Barrier(10)

    def attempt(index):
        with session() as db:
            db.execute(text("SET LOCAL lock_timeout = '10s'"))
            barrier.wait(timeout=10)
            try:
                row = reserve(
                    db,
                    QuotaDelta(SCOPE, 'deployment', f'candidate-{index}', 'terraform.apply', {'vm_count': 1}),
                    f'quota-race-{index}',
                    1,
                )
                db.commit()
                return ('won', row.id)
            except HTTPException as error:
                db.rollback()
                return ('rejected', error.detail['code'])

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(attempt, range(10)))

    assert sum(kind == 'won' for kind, _ in results) == 1
    assert sum(kind == 'rejected' for kind, _ in results) == 9
    assert {value for kind, value in results if kind == 'rejected'} == {'PROJECT_QUOTA_EXCEEDED'}

    with session() as db:
        usage = db.scalar(select(ProjectQuotaUsage).where(
            ProjectQuotaUsage.tenant_id == SCOPE.tenant_id,
            ProjectQuotaUsage.project_id == SCOPE.project_id,
            ProjectQuotaUsage.dimension == 'vm_count',
        ))
        assert usage.used == 9
        assert usage.reserved == 1

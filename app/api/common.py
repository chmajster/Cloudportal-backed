import json
import hmac
import hashlib
import uuid
from datetime import datetime
from typing import Annotated
from fastapi import HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.models import Idempotency
from app.security.core import encryption_key


Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


def public(obj, fields):
    result = {key: getattr(obj, key) for key in fields.split()}
    return {k: v.isoformat() + 'Z' if isinstance(v, datetime) else v for k, v in result.items()}


def find(db, model, key):
    obj = db.get(model, key)
    if obj is None:
        raise HTTPException(404, 'Resource not found')
    return obj


def paginate(db, model, offset=0, limit=100, predicate=None):
    query = select(model)
    if predicate is not None:
        query = query.where(predicate)
    return db.scalars(query.order_by(model.id.desc()).offset(offset).limit(limit)).unique().all()


def idempotent(db, request, actor, payload, create, *, required=False):
    key = request.headers.get('Idempotency-Key')
    if not key:
        if required:
            raise HTTPException(400, 'Idempotency-Key UUID is required')
        return create()
    try:
        key = str(uuid.UUID(key))
    except ValueError:
        raise HTTPException(400, 'Idempotency-Key must be a UUID') from None
    fingerprint = hmac.new(encryption_key(), json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str).encode(), hashlib.sha256).hexdigest()
    row = Idempotency(user_id=actor.user_id, path=request.url.path, key=key, fingerprint=fingerprint)
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        row = db.scalar(select(Idempotency).where(Idempotency.user_id == actor.user_id, Idempotency.path == request.url.path, Idempotency.key == key))
        if row.fingerprint != fingerprint:
            raise HTTPException(409, 'Idempotency-Key was used for a different request')
        if row.response is None:
            raise HTTPException(409, 'Request still in progress')
        return row.response
    result = create()
    # Never persist plaintext token/reset secrets in idempotency records.
    row.response = jsonable_encoder({k: v for k, v in result.items() if k not in {'token', 'reset_token', 'secret'}})
    return result

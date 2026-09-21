from sqlalchemy import func, select

from app.events.registry import event_matches
from app.events.service import event_public
from app.models import EventConsumer, EventRecord, now


def consumer_public(db, row: EventConsumer) -> dict:
    latest = db.scalar(select(func.max(EventRecord.sequence))) or 0
    return {
        'id': row.id,
        'name': row.name,
        'event_patterns': row.event_patterns,
        'cursor_sequence': row.cursor_sequence,
        'last_checkpoint_sequence': row.last_checkpoint_sequence,
        'lag': max(0, latest - row.cursor_sequence),
        'max_batch': row.max_batch,
        'is_active': row.is_active,
        'owner_user_id': row.owner_user_id,
        'created_by': row.created_by,
        'last_polled_at': row.last_polled_at,
        'last_acked_at': row.last_acked_at,
        'created_at': row.created_at,
        'updated_at': row.updated_at,
    }


def retained_event_floor(db) -> int:
    earliest = db.scalar(select(func.min(EventRecord.sequence)))
    return max(0, (earliest or 1) - 1)


def resolve_start_sequence(db, start_from: str, start_sequence: int | None) -> int:
    latest = db.scalar(select(func.max(EventRecord.sequence))) or 0
    floor = retained_event_floor(db)
    if start_from == 'latest':
        return latest
    if start_from == 'earliest':
        return floor
    if start_from == 'sequence':
        if start_sequence is None:
            raise ValueError('start_sequence is required when start_from=sequence')
        if start_sequence < floor or start_sequence > latest:
            raise ValueError('start_sequence is outside the retained event range')
        return start_sequence
    raise ValueError('Unsupported consumer start position')


def poll_consumer(db, row: EventConsumer, requested_limit: int | None = None) -> dict:
    if not row.is_active:
        raise ValueError('Event consumer is disabled')

    floor = retained_event_floor(db)
    if row.cursor_sequence < floor:
        raise ValueError('Consumer cursor is behind retained event floor; reset required')

    limit = min(requested_limit or row.max_batch, row.max_batch, 500)
    scan_window = min(max(limit * 20, 1000), 10000)
    events = db.scalars(
        select(EventRecord)
        .where(EventRecord.sequence > row.cursor_sequence)
        .order_by(EventRecord.sequence.asc())
        .limit(scan_window)
    ).all()

    matched = []
    checkpoint = row.cursor_sequence
    for event in events:
        checkpoint = event.sequence
        if event_matches(row.event_patterns or (), event.type):
            matched.append(event_public(event))
            if len(matched) >= limit:
                break

    row.last_polled_at = now()
    row.last_checkpoint_sequence = checkpoint
    return {
        'consumer_id': row.id,
        'cursor_sequence': row.cursor_sequence,
        'checkpoint_sequence': checkpoint,
        'retained_floor_sequence': floor,
        'items': matched,
        'has_scanned_events': bool(events),
        'scan_window': scan_window,
    }


def ack_consumer(db, row: EventConsumer, sequence: int) -> EventConsumer:
    if not row.is_active:
        raise ValueError('Event consumer is disabled')
    latest = db.scalar(select(func.max(EventRecord.sequence))) or 0
    if sequence < row.cursor_sequence:
        raise ValueError('ACK sequence cannot move the cursor backwards')
    if sequence > latest:
        raise ValueError('ACK sequence is beyond the latest event')
    if row.last_checkpoint_sequence is None or sequence > row.last_checkpoint_sequence:
        raise ValueError('ACK sequence exceeds the last polled checkpoint')
    row.cursor_sequence = sequence
    row.last_acked_at = now()
    return row


def reset_consumer(db, row: EventConsumer, start_from: str, sequence: int | None) -> EventConsumer:
    row.cursor_sequence = resolve_start_sequence(db, start_from, sequence)
    row.last_acked_at = None
    row.last_polled_at = None
    row.last_checkpoint_sequence = None
    return row

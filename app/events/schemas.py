from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from sqlalchemy import select

from app.models import EventSchema


class EventSchemaValidationError(ValueError):
    pass


def validate_schema_document(document: dict) -> None:
    if not isinstance(document, dict):
        raise EventSchemaValidationError('Event schema must be a JSON object')
    try:
        Draft202012Validator.check_schema(document)
    except SchemaError as exc:
        path = '.'.join(str(part) for part in exc.path)
        suffix = f' at {path}' if path else ''
        raise EventSchemaValidationError('Invalid JSON Schema' + suffix) from None


def validate_event_payload(db, event_type: str, version: int, payload: dict):
    row = db.scalar(
        select(EventSchema).where(
            EventSchema.event_type == event_type,
            EventSchema.version == version,
            EventSchema.is_active.is_(True),
        )
    )
    if row is None:
        return None
    try:
        Draft202012Validator(row.schema_json).validate(payload)
    except ValidationError as exc:
        path = '.'.join(str(part) for part in exc.absolute_path)
        suffix = f' at {path}' if path else ''
        raise EventSchemaValidationError(
            f'Event payload does not match {event_type} v{version}{suffix}'
        ) from None
    return row


def schema_public(row: EventSchema) -> dict:
    return {
        'id': row.id,
        'event_type': row.event_type,
        'version': row.version,
        'schema': row.schema_json,
        'compatibility': row.compatibility,
        'is_active': row.is_active,
        'created_by': row.created_by,
        'created_at': row.created_at,
        'updated_at': row.updated_at,
    }

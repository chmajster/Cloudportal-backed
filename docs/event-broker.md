# Event broker and extensibility

Cloudportal-backed uses PostgreSQL as the durable source of truth for domain events.
A domain mutation and its `EventRecord` are committed in the same database
transaction. Redis/RQ remains the execution queue for infrastructure jobs; it is
not the event system of record.

## Event envelope

Each event has an immutable sequence, UUID, type, schema version, source, subject,
payload, metadata, request ID, correlation ID, causation ID and actor identifiers.
The HTTP webhook bridge emits a CloudEvents-compatible 1.0 envelope while retaining
the legacy top-level `event`, `job`, `recovery` and `alert` fields.

Built-in API actions already using the audit boundary automatically publish safe
domain event metadata. Job, recovery and system alert events use the same broker.
Request bodies, credentials, token values, exception strings and encrypted secrets
are never copied into audit-derived event payloads.

## Delivery model

The dispatcher performs two independent stages:

1. materialize matching `ExtensionDelivery` rows from the durable event log;
2. execute pending extension deliveries.

A normal materialization has a unique consumer+sequence key, making it idempotent
across dispatcher restarts. Consumer failures use exponential backoff and move to
`dead_letter` after eight failed attempts. Dead letters can be explicitly
requeued. Manual replay creates independent replay deliveries and therefore may be
performed multiple times.

The built-in `core.webhook-bridge` extension converts broker events into
`WebhookDelivery` rows. Normal delivery uses a private routing snapshot captured
transactionally when the event is published, so later webhook creation or
subscription changes never apply retroactively to backlog events. Explicit replay
uses the currently active webhook subscriptions. Existing HTTPS allowlisting, HMAC
signing, redirect blocking and retry handling remain unchanged.

## Subscription patterns

Webhook endpoints and code extensions support exact names and fnmatch-style event
patterns. Common examples:

- `job.successful`
- `job.*`
- `custom.*`
- `*`

Custom publishers exposed through the API are restricted to the `custom.*`
namespace. Internal trusted code can publish first-party event names.

## Extension SDK

Extensions are trusted application code placed in
`app/extensions/extension_<name>.py`. The module exports a tuple named
`EXTENSIONS` containing `ExtensionSpec` objects.

```python
from app.events.contracts import ExtensionSpec

def on_vm_event(db, event, delivery):
    # Durable handler: write through the supplied transaction.
    # delivery.is_replay distinguishes normal consumption from explicit replay.
    ...

EXTENSIONS = (
    ExtensionSpec(
        name='example.inventory-sync',
        version='1.0.0',
        description='Synchronize VM changes with an external inventory.',
        event_patterns=('vm.*', 'inventory.*'),
        handler=on_vm_event,
    ),
)
```

The runtime deliberately does not accept uploaded Python, shell commands or
arbitrary import paths through the API. Installation is a deployment concern;
runtime configuration can only enable/disable an installed extension and persist
its JSON configuration.

## API

- `GET /api/v1/events` – event log with filtering and cursor-style
  `after_sequence`.
- `POST /api/v1/events` – publish an idempotent `custom.*` event.
- `POST /api/v1/events/{id}/replay` – replay to all, webhook, or non-webhook
  extensions.
- `GET /api/v1/event-deliveries` – delivery diagnostics.
- `GET /api/v1/event-dead-letters` – DLQ view.
- `POST /api/v1/event-deliveries/{kind}/{id}/retry` – requeue a terminal
  delivery.
- `GET /api/v1/extensions` – installed extensions and health.
- `PUT /api/v1/extensions/{name}` – enable/disable/configure an extension.

RBAC separates read, publish, replay and extension management:
`events.read`, `events.publish`, `events.replay`, `extensions.read`,
`extensions.manage`.


## Event schema registry

The broker supports optional versioned JSON Schema Draft 2020-12 contracts.

- `POST /api/v1/event-schemas` registers an immutable event type + version contract.
- `GET /api/v1/event-schemas` lists registered contracts.
- `PUT /api/v1/event-schemas/{id}/state` enables or disables enforcement.
- `POST /api/v1/event-schemas/{id}/validate` validates a sample payload.

When an active schema exists for the exact `event_type` and `schema_version`,
`publish_event()` validates the payload before writing the event. This applies to
both API-published custom events and trusted internal publishers. Invalid payloads
are rejected transactionally. External/remote `$ref` values are rejected; local
references such as `#/definitions/...` remain supported.

Schema mutation requires `events.manage`; reading schemas uses `events.read`.

## Durable pull consumers

External integrations can use PostgreSQL-backed consumers instead of maintaining
their own cursor.

- `POST /api/v1/event-consumers` creates a durable consumer.
- `GET /api/v1/event-consumers/{id}/events` scans forward from the ACKed cursor.
- `POST /api/v1/event-consumers/{id}/ack` advances the cursor monotonically.
- `POST /api/v1/event-consumers/{id}/reset` resets to earliest/latest/sequence.
- `PUT /api/v1/event-consumers/{id}` updates patterns, owner, batch size or state.

Each poll returns two positions:

- `cursor_sequence`: last ACKed sequence;
- `checkpoint_sequence`: furthest sequence actually scanned by that poll.

The consumer may ACK any position from its current cursor through the returned
checkpoint. ACK cannot move backwards and cannot jump beyond a polled checkpoint.
This allows sparse wildcard filters to advance across unmatched events without
silently skipping unscanned data.

Consumers support exact names plus prefix wildcards such as `job.*`,
`custom.*`, and `*`. A consumer is owned by one user/service account.
`events.consume` permits polling/ACK for owned consumers; `events.manage`
permits administrative creation, ownership changes, reset and deletion.

## Event retention

`CP_RETENTION_EVENTS_DAYS` controls the minimum age for event-log deletion.
Retention is cursor-aware: an old event is removed only when it is not newer than
the slowest active pull consumer / enabled extension cursor and it is not referenced
by a pending or dead-letter extension delivery. Extension states are synchronized
before retention runs so a freshly installed replaying extension cannot lose its
backlog.

`CP_EVENT_CONSUMER_LAG_WARNING` controls the lag threshold exposed through
`/alerts`. Prometheus exports active schema/consumer counts and
`cloudportal_event_consumer_lag{consumer="..."}`.

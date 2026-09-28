"""Extensible Event Broker action handlers.

Only registered handlers can execute. Configuration is data; it is never imported,
eval'ed or passed to a shell.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urljoin

import httpx

from app.awx import AwxClient
from app.credentials.service import ensure_credential_usable
from app.events.security import pinned_url, redact, resolve_webhook_target
from app.events.templates import render_template
from app.models import Credential
from app.resource_scope.authorization import Scope
from app.resource_scope.database import reference_visible
from app.security.core import decrypt_blob, decrypt_secret


ALLOWED_WEBHOOK_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
ALLOWED_DECISIONS = frozenset({"CONTINUE", "BLOCK", "MODIFY", "REQUIRE_APPROVAL", "FAIL"})


class ActionFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        response_code: int | None = None,
        response_body: str | None = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.response_code = response_code
        self.response_body = response_body


@dataclass(slots=True)
class ActionResult:
    response_code: int | None = None
    response_headers: dict = field(default_factory=dict)
    response_body: str | None = None
    decision: str = "CONTINUE"
    reason: str = ""
    patch: dict = field(default_factory=dict)
    external_job_id: str | None = None


class EventActionHandler(Protocol):
    action_type: str

    def validate(self, config: dict, *, phase: str) -> None: ...
    def execute(self, db, subscription, envelope: dict, delivery_id: str) -> ActionResult: ...


def _template_context(envelope: dict) -> dict:
    return {**envelope, "event": envelope}


def _subscription_secret(subscription) -> dict:
    if not subscription.encrypted_action_secret:
        return {}
    try:
        raw = decrypt_blob(
            subscription.encrypted_action_secret,
            f"event-subscription:{subscription.id}",
        )
        value = json.loads(raw.decode())
    except Exception:
        raise ActionFailure("Event subscription secret cannot be decrypted") from None
    if not isinstance(value, dict):
        raise ActionFailure("Event subscription secret has invalid structure")
    return value


def _credential(db, subscription, envelope: dict) -> tuple[Credential | None, dict]:
    if subscription.credential_id is None:
        return None, {}
    credential = db.get(Credential, subscription.credential_id)
    if credential is None:
        raise ActionFailure("Event subscription credential no longer exists")
    ensure_credential_usable(credential)
    scope = envelope.get("scope") or {}
    tenant_id, project_id = scope.get("organization_id"), scope.get("project_id")
    if tenant_id and project_id:
        if not reference_visible(
            db, "credential", credential.id, Scope(str(tenant_id), str(project_id))
        ):
            raise ActionFailure("Credential access for the event project has been revoked")
    return credential, decrypt_secret(credential)


def _header_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, item in value.items():
        name = str(key).strip()
        if not name or "\r" in name or "\n" in name:
            raise ActionFailure("Webhook header name is invalid")
        text = str(item)
        if "\r" in text or "\n" in text:
            raise ActionFailure("Webhook header value is invalid")
        result[name] = text
    return result


def _safe_response_headers(headers) -> dict:
    blocked = {"authorization", "proxy-authorization", "set-cookie", "cookie"}
    result = {}
    for key, value in list(headers.items())[:50]:
        result[str(key)] = "[REDACTED]" if str(key).lower() in blocked else str(value)[:1000]
    return result


class WebhookActionHandler:
    action_type = "webhook"

    def validate(self, config: dict, *, phase: str) -> None:
        method = str(config.get("method") or "POST").upper()
        if method not in ALLOWED_WEBHOOK_METHODS:
            raise ValueError("Unsupported webhook method")
        if not str(config.get("url") or "").strip():
            raise ValueError("Webhook URL is required")
        timeout = int(config.get("timeout_seconds") or 30)
        if not 1 <= timeout <= 120:
            raise ValueError("Webhook timeout must be between 1 and 120 seconds")
        if config.get("follow_redirects") and int(config.get("max_redirects") or 3) > 5:
            raise ValueError("Webhook max_redirects cannot exceed 5")

    def _authentication(self, db, subscription, envelope, headers):
        config = subscription.action_config or {}
        secret = _subscription_secret(subscription)
        credential, credential_secret = _credential(db, subscription, envelope)
        auth_mode = str(config.get("authentication") or "none").lower()
        if auth_mode == "none":
            pass
        elif auth_mode == "bearer":
            token = secret.get("bearer_token") or credential_secret.get("token") or credential_secret.get("api_key")
            if not token:
                raise ActionFailure("Bearer credential does not contain a token")
            headers["Authorization"] = "Bearer " + str(token)
        elif auth_mode == "basic":
            username = secret.get("username") or (credential.username if credential else "")
            password = secret.get("password") or credential_secret.get("password")
            if not username or not password:
                raise ActionFailure("Basic authentication requires username and password")
            import base64
            headers["Authorization"] = "Basic " + base64.b64encode(
                f"{username}:{password}".encode()
            ).decode()
        elif auth_mode == "api_key":
            value = secret.get("api_key") or credential_secret.get("api_key") or credential_secret.get("token")
            if not value:
                raise ActionFailure("API key credential does not contain a key")
            header = str(config.get("api_key_header") or "X-API-Key")
            headers[header] = str(value)
        else:
            raise ActionFailure("Unsupported webhook authentication mode")

        for key, value in _header_map(secret.get("headers")).items():
            headers[key] = value

    def _send_once(self, db, subscription, envelope, delivery_id, target_url):
        config = subscription.action_config or {}
        target = resolve_webhook_target(db, target_url)
        method = str(config.get("method") or "POST").upper()
        context = _template_context(envelope)
        payload_template = config.get("payload_template")
        payload = render_template(
            envelope if payload_template in (None, "") else payload_template,
            context,
        )
        body = json.dumps(payload, separators=(",", ":"), default=str).encode()

        timestamp = str(int(time.time()))
        headers = {
            "Accept": "application/json",
            "User-Agent": "CloudPortal-EventBroker/1.0",
            "X-CloudPortal-Event-ID": str(envelope["id"]),
            "X-CloudPortal-Delivery-ID": str(delivery_id),
            "X-CloudPortal-Event-Type": str(envelope["type"]),
            "X-CloudPortal-Timestamp": timestamp,
        }
        if method not in {"GET", "DELETE"} or payload_template not in (None, ""):
            headers["Content-Type"] = "application/json"
        headers.update(_header_map(config.get("headers")))
        self._authentication(db, subscription, envelope, headers)
        secret = _subscription_secret(subscription)
        if secret.get("hmac_secret"):
            signature = hmac.new(
                str(secret["hmac_secret"]).encode(),
                timestamp.encode() + b"." + body,
                hashlib.sha256,
            ).hexdigest()
            headers["X-CloudPortal-Signature"] = "sha256=" + signature

        parsed = target["parsed"]
        original_host = target["host"]
        host_header = original_host
        default_port = 443 if parsed.scheme == "https" else 80
        if parsed.port and parsed.port != default_port:
            host_header += f":{parsed.port}"

        last_error = None
        for address in target["addresses"]:
            try:
                with httpx.Client(
                    timeout=float(config.get("timeout_seconds") or subscription.timeout_seconds or 30),
                    verify=bool(config.get("verify_tls", True)),
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    request_headers = dict(headers)
                    request_headers["Host"] = host_header
                    request = client.build_request(
                        method,
                        pinned_url(parsed, address),
                        headers=request_headers,
                        content=body if method not in {"GET"} or payload_template not in (None, "") else None,
                    )
                    # httpcore consumes this extension as the TLS SNI/certificate hostname,
                    # while the TCP destination remains the prevalidated IP.
                    request.extensions["sni_hostname"] = original_host
                    response = client.send(request, follow_redirects=False)
                return response
            except (httpx.TimeoutException, httpx.NetworkError, httpx.ProtocolError) as exc:
                last_error = exc
                continue
        raise ActionFailure(
            "Webhook connection failed: " + type(last_error).__name__,
            retryable=True,
        )

    def execute(self, db, subscription, envelope: dict, delivery_id: str) -> ActionResult:
        config = subscription.action_config or {}
        url = str(render_template(str(config.get("url") or ""), _template_context(envelope)))
        redirects = 0
        while True:
            response = self._send_once(db, subscription, envelope, delivery_id, url)
            if response.status_code in {301, 302, 303, 307, 308} and config.get("follow_redirects"):
                redirects += 1
                if redirects > int(config.get("max_redirects") or 3):
                    raise ActionFailure("Webhook redirect limit exceeded")
                location = response.headers.get("location")
                if not location:
                    raise ActionFailure("Webhook redirect has no Location header")
                url = urljoin(url, location)
                # The next iteration resolves and validates the new target again.
                continue
            break

        limit = resolve_webhook_target(db, url)["policy"]["response_body_limit"]
        content = response.content[: limit + 1]
        truncated = len(content) > limit
        if truncated:
            content = content[:limit]
        text = content.decode(response.encoding or "utf-8", errors="replace")
        if truncated:
            text += "\n[TRUNCATED]"

        if response.status_code == 429 or 500 <= response.status_code <= 599:
            raise ActionFailure(
                f"Webhook returned HTTP {response.status_code}",
                retryable=True,
                response_code=response.status_code,
                response_body=text,
            )
        if not 200 <= response.status_code <= 299:
            raise ActionFailure(
                f"Webhook returned HTTP {response.status_code}",
                retryable=False,
                response_code=response.status_code,
                response_body=text,
            )

        result = ActionResult(
            response_code=response.status_code,
            response_headers=_safe_response_headers(response.headers),
            response_body=text,
        )
        if subscription.is_blocking:
            try:
                decoded = response.json() if text else {}
            except ValueError:
                raise ActionFailure("Blocking webhook returned malformed JSON") from None
            if not isinstance(decoded, dict):
                raise ActionFailure("Blocking webhook response must be an object")
            decision = str(decoded.get("decision") or "CONTINUE").upper()
            if decision not in ALLOWED_DECISIONS:
                raise ActionFailure("Blocking webhook returned an invalid decision")
            result.decision = decision
            result.reason = str(decoded.get("reason") or "")[:1000]
            patch = decoded.get("patch") or {}
            if not isinstance(patch, dict):
                raise ActionFailure("Blocking webhook MODIFY patch must be an object")
            result.patch = patch
        return result


class ApprovalActionHandler:
    action_type = "approval"

    def validate(self, config: dict, *, phase: str) -> None:
        if phase != "PRE":
            raise ValueError("Approval actions are only valid for PRE subscriptions")

    def execute(self, db, subscription, envelope: dict, delivery_id: str) -> ActionResult:
        reason = render_template(
            str((subscription.action_config or {}).get("reason") or "Event Broker approval required"),
            _template_context(envelope),
        )
        return ActionResult(decision="REQUIRE_APPROVAL", reason=str(reason)[:1000])


class PublishEventActionHandler:
    action_type = "publish_event"

    def validate(self, config: dict, *, phase: str) -> None:
        event_type = str(config.get("event_type") or "")
        if not event_type.startswith("custom."):
            raise ValueError("Publish Event action may publish only custom.* events")

    def execute(self, db, subscription, envelope: dict, delivery_id: str) -> ActionResult:
        from app.events.service import publish_event

        config = subscription.action_config or {}
        event_type = str(config["event_type"])
        payload = render_template(
            config.get("payload_template") or {"source_event_id": "{{ event.id }}"},
            _template_context(envelope),
        )
        correlation = envelope.get("correlation") or {}
        scope = envelope.get("scope") or {}
        resource = envelope.get("resource") or {}
        publish_event(
            db,
            event_type,
            redact(payload),
            subject_type=str(resource.get("resource_type") or "event"),
            subject_id=str(resource.get("resource_id") or envelope["id"]),
            source="cloudportal.event-broker",
            correlation_id=correlation.get("correlation_id"),
            causation_id=envelope["id"],
            context={
                "scope": scope,
                "resource": resource,
                "correlation": {
                    **correlation,
                    "parent_event_id": envelope["id"],
                    "root_event_id": correlation.get("root_event_id") or envelope["id"],
                    "depth": int(correlation.get("depth") or 0) + 1,
                },
            },
        )
        return ActionResult()


class AwxActionHandler:
    action_type = "awx"

    def validate(self, config: dict, *, phase: str) -> None:
        if phase != "POST":
            raise ValueError("AWX actions are asynchronous POST actions")
        if not config.get("job_template_id"):
            raise ValueError("AWX job_template_id is required")

    def execute(self, db, subscription, envelope: dict, delivery_id: str) -> ActionResult:
        credential, secret = _credential(db, subscription, envelope)
        if credential is None or credential.type != "awx":
            raise ActionFailure("AWX action requires an AWX credential")
        config = subscription.action_config or {}
        client = AwxClient(
            credential.endpoint,
            verify_ssl=credential.verify_ssl,
            token=secret.get("token"),
            username=credential.username,
            password=secret.get("password"),
        )
        context = _template_context(envelope)
        extra_vars = render_template(config.get("extra_vars") or {}, context)
        if not isinstance(extra_vars, dict):
            raise ActionFailure("AWX extra_vars template must render to an object")
        body = {"extra_vars": extra_vars}
        inventory_id = config.get("inventory_id")
        if inventory_id:
            body["inventory"] = int(inventory_id)
        limit = config.get("limit_template")
        if limit:
            body["limit"] = str(render_template(str(limit), context))
        try:
            response = client.request(
                "POST",
                f"job_templates/{int(config['job_template_id'])}/launch/",
                json=body,
            )
            data = response.json()
        except Exception as exc:
            raise ActionFailure(
                "AWX job launch failed: " + type(exc).__name__,
                retryable=True,
            ) from None
        job_id = data.get("id") if isinstance(data, dict) else None
        if not job_id:
            raise ActionFailure("AWX response did not include a job id")
        return ActionResult(
            response_code=getattr(response, "status_code", None),
            response_body=json.dumps(redact(data), separators=(",", ":"))[:65536],
            external_job_id=str(job_id),
        )


_HANDLERS = {
    handler.action_type: handler
    for handler in (
        WebhookActionHandler(),
        ApprovalActionHandler(),
        PublishEventActionHandler(),
        AwxActionHandler(),
    )
}


def action_handler(action_type: str) -> EventActionHandler:
    handler = _HANDLERS.get(str(action_type))
    if handler is None:
        raise ValueError("Unsupported Event Broker action type")
    return handler


def action_types() -> list[str]:
    return sorted(_HANDLERS)

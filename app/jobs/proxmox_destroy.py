import time

from fastapi import HTTPException
from sqlalchemy import select

from app.database import session
from app.executors.base import ExecutionFailed
from app.inventory_sync import state_outputs
from app.models import Deployment, ManagedVM
from app.providers.registry import provider_for
from app.terraform.state import read_stored_state


STOP_TIMEOUT_SECONDS = 120
STOP_POLL_SECONDS = 2


def _error_summary(error):
    if isinstance(error, HTTPException):
        detail = error.detail
        return detail[:240] if isinstance(detail, str) else f'HTTP {error.status_code}'
    return error.__class__.__name__


def _vm_identity(context):
    deployment = context.deployment
    if deployment is None or deployment.provider != 'proxmox':
        return None

    with session() as db:
        managed = db.scalar(
            select(ManagedVM)
            .where(
                ManagedVM.deployment_id == deployment.id,
                ManagedVM.lifecycle_status != 'destroyed',
            )
            .order_by(ManagedVM.updated_at.desc())
            .limit(1)
        )
        if managed is not None:
            return str(managed.node), int(managed.vm_id)

        current = db.get(Deployment, deployment.id)
        variables = dict((current.variables if current is not None else deployment.variables) or {})
        node = str(variables.get('node') or '')
        vm_id = variables.get('vm_id') or variables.get('vmid')

        if vm_id in {None, ''}:
            try:
                payload = read_stored_state(db, deployment.id)
                outputs = state_outputs(payload) if payload else {}
                vm_id = (outputs.get('vm_id') or {}).get('value')
            except (RuntimeError, TypeError, ValueError):
                vm_id = None

    if vm_id in {None, ''}:
        context.log('terraform.destroy.force_stop.skipped: provider VM identity is unavailable')
        return None
    if not node:
        raise ExecutionFailed('Cannot hard-stop Proxmox VM before destroy: node is missing')
    try:
        return node, int(vm_id)
    except (TypeError, ValueError):
        raise ExecutionFailed('Cannot hard-stop Proxmox VM before destroy: VMID is invalid') from None


def _wait_task(context, provider, node, task, timeout):
    if not isinstance(task, str) or not task:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        context.check()
        status = provider.task_status(node, task) or {}
        if str(status.get('status') or '').lower() == 'stopped':
            exit_status = str(status.get('exitstatus') or '')
            if exit_status == 'OK':
                return
            raise ExecutionFailed(
                'Proxmox hard-stop task failed: ' + (exit_status or 'unknown exit status')
            )
        time.sleep(STOP_POLL_SECONDS)
    raise ExecutionFailed('Timed out waiting for Proxmox hard-stop task')


def force_stop_before_destroy(context, timeout=STOP_TIMEOUT_SECONDS):
    """Hard-stop a live Proxmox VM before Terraform destroy.

    Uses only the Proxmox API. QEMU Guest Agent, guest networking and guest OS
    responsiveness are intentionally irrelevant.
    """
    identity = _vm_identity(context)
    if identity is None:
        return False

    node, vm_id = identity
    provider = provider_for(context.credential)
    try:
        status = provider.vm_status(node, vm_id) or {}
    except HTTPException as error:
        if error.status_code == 404:
            context.log(f'terraform.destroy.force_stop.absent: {node} / VMID {vm_id}')
            return False
        raise ExecutionFailed(
            'Cannot verify Proxmox VM power state before destroy: ' + _error_summary(error)
        ) from None
    except Exception as error:
        raise ExecutionFailed(
            'Cannot verify Proxmox VM power state before destroy: ' + _error_summary(error)
        ) from None

    power_state = str(status.get('status') or '').lower()
    context.log(
        f'terraform.destroy.force_stop.status: {node} / VMID {vm_id} '
        f'status={power_state or "unknown"}'
    )
    if power_state == 'stopped':
        context.log(f'terraform.destroy.force_stop.already_stopped: {node} / VMID {vm_id}')
        return False

    context.stage('terraform.destroy.force_stop')
    context.log(
        f'terraform.destroy.force_stop.requested: {node} / VMID {vm_id}; '
        'Proxmox hard stop, QEMU Guest Agent bypassed'
    )
    try:
        task = provider.vm_power(node, vm_id, 'stop')
        _wait_task(context, provider, node, task, min(max(1, int(timeout)), STOP_TIMEOUT_SECONDS))
    except HTTPException as error:
        if error.status_code == 404:
            context.log(f'terraform.destroy.force_stop.absent_after_request: {node} / VMID {vm_id}')
            return True
        raise ExecutionFailed('Proxmox hard stop failed: ' + _error_summary(error)) from None
    except ExecutionFailed:
        raise
    except Exception as error:
        raise ExecutionFailed('Proxmox hard stop failed: ' + _error_summary(error)) from None

    deadline = time.monotonic() + min(max(1, int(timeout)), STOP_TIMEOUT_SECONDS)
    while time.monotonic() < deadline:
        context.check()
        try:
            current = provider.vm_status(node, vm_id) or {}
        except HTTPException as error:
            if error.status_code == 404:
                context.log(f'terraform.destroy.force_stop.absent_after_stop: {node} / VMID {vm_id}')
                return True
            raise ExecutionFailed(
                'Cannot confirm Proxmox VM stopped state: ' + _error_summary(error)
            ) from None
        except Exception as error:
            raise ExecutionFailed(
                'Cannot confirm Proxmox VM stopped state: ' + _error_summary(error)
            ) from None

        if str(current.get('status') or '').lower() == 'stopped':
            context.log(f'terraform.destroy.force_stop.completed: {node} / VMID {vm_id}')
            return True
        time.sleep(STOP_POLL_SECONDS)

    raise ExecutionFailed(
        f'Proxmox VM {node}/{vm_id} did not reach stopped state before Terraform destroy'
    )

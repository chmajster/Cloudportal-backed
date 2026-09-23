"""First-boot Cloud-init contracts shared by API validation and execution.

This module never resolves guest secrets and never modifies a running guest.
A Cloud-init declaration is compiled before plan/apply, not executed over SSH.
"""

from pathlib import Path
from typing import Any, Mapping, Sequence


NODE_SSH_VARIABLES = (
    'PROXMOX_VE_SSH_USERNAME',
    'PROXMOX_VE_SSH_PASSWORD',
    'PROXMOX_VE_SSH_PRIVATE_KEY',
    'PROXMOX_VE_SSH_AGENT',
    'PROXMOX_VE_SSH_AUTH_SOCK',
)


def blueprint_snapshot(context) -> dict:
    """Prefer the immutable job snapshot, retaining deployment-only callers."""
    payload = getattr(context.job, 'payload', None) or {}
    return payload.get('blueprint') or ((context.deployment.workflow or {}).get('blueprint') or {})


def validate_cloud_init_workflow(steps: Sequence[Mapping[str, Any]]) -> bool:
    """Return whether first-boot Cloud-init is selected; reject unsafe ordering."""
    cloud_steps = [step for step in steps if step.get('type') == 'cloud_init']
    if not cloud_steps:
        return False
    if len(cloud_steps) != 1:
        raise ValueError('Cloud-init workflow must contain exactly one cloud_init declaration')
    declaration = cloud_steps[0]
    if declaration.get('conditions') or declaration.get('retry') or declaration.get('rollback'):
        raise ValueError('Cloud-init is a pre-boot declaration and cannot use conditions, retry or rollback')
    cloud_id = declaration.get('id')
    graph = {step.get('id'): list(step.get('depends_on') or []) for step in steps}
    if not cloud_id or len(graph) != len(steps) or None in graph or '' in graph:
        raise ValueError('Cloud-init workflow has missing or duplicate step IDs')
    visited, visiting = set(), set()

    def visit(identifier):
        if identifier not in graph:
            raise ValueError('Cloud-init workflow references a missing dependency')
        if identifier in visiting:
            raise ValueError('Cloud-init workflow contains a dependency cycle')
        if identifier in visited:
            return
        visiting.add(identifier)
        for parent in graph[identifier]:
            visit(parent)
        visiting.remove(identifier)
        visited.add(identifier)

    for identifier in graph:
        visit(identifier)

    def ancestors(identifier):
        result, pending = set(), list(graph[identifier])
        while pending:
            parent = pending.pop()
            if parent not in result:
                result.add(parent)
                pending.extend(graph[parent])
        return result

    # Approved plans already contain Cloud-init. Preparing it after plan would
    # silently make the preview differ from the plan that is actually applied.
    for step in steps:
        if step.get('type') in {'terraform_plan', 'terraform_apply'}:
            if cloud_id not in ancestors(step.get('id')):
                raise ValueError('Cloud-init must be an ancestor of Terraform plan and apply')
    return True


def validate_cloud_init_workspace(workspace: Path) -> None:
    """Do not discard a legacy bootstrap identity or rewrite its saved plan."""
    if any((Path(workspace) / name).exists() for name in (
        '.cloudportal-qemu-bootstrap.json', '.cloudportal-qemu-bootstrap-key',
    )):
        raise ValueError(
            'Existing guest-bootstrap state requires recovery before switching to first-boot Cloud-init; '
            'the temporary credential and saved plan have not been changed'
        )


def node_ssh_environment(env: dict, source: Mapping[str, str], secret: Mapping[str, Any], username: str) -> None:
    """Copy only node SSH settings, never a selected guest credential."""
    for name in NODE_SSH_VARIABLES:
        if source.get(name):
            env[name] = source[name]
    if secret.get('password') and not env.get('PROXMOX_VE_SSH_PASSWORD'):
        env['PROXMOX_VE_SSH_USERNAME'] = str(username).split('@', 1)[0]
        env['PROXMOX_VE_SSH_PASSWORD'] = secret['password']


def validate_snippet_storage(storages: Sequence[Mapping[str, Any]], selected: str | None) -> None:
    if not selected:
        raise ValueError(
            'Cloud-init QEMU Guest Agent installation requires a snippets storage. '
            'Select it in Workflow > Cloud-init; SSH guest-bootstrap fallback is disabled'
        )
    storage = next((row for row in storages if str(row.get('storage') or row.get('id')) == selected), None)
    if storage is None:
        raise ValueError('Cloud-init snippets storage is not available on the target Proxmox node')
    content = storage.get('content') or []
    content = content.split(',') if isinstance(content, str) else content
    if 'snippets' not in {str(value).strip() for value in content}:
        raise ValueError('Selected Cloud-init storage does not support snippets')
    if storage.get('disable') in (True, 1, '1', 'true'):
        raise ValueError('Selected Cloud-init snippets storage is disabled')
    if storage.get('active') in (False, 0, '0') or storage.get('enabled') in (False, 0, '0'):
        raise ValueError('Selected Cloud-init snippets storage is inactive')

import time

from fastapi import HTTPException
from sqlalchemy import select

from app.database import session
from app.executors.base import Cancelled, ExecutionFailed
from app.models import Credential, Job, ManagedVM, Provider
from app.providers.registry import provider_for


OPERATION = 'proxmox.clone_template'
RUNTIME_KEY = '_proxmox_clone_template_runtime'
RESULT_KEY = '_proxmox_clone_template_result'
POLL_SECONDS = 2


def _runtime(context):
    return dict((context.job.payload or {}).get(RUNTIME_KEY) or {})


def _persist(context, **changes):
    with session() as db:
        job = db.get(Job, context.job.id)
        if job is None:
            raise ExecutionFailed('Clone-to-template job disappeared')
        payload = dict(job.payload or {})
        runtime = dict(payload.get(RUNTIME_KEY) or {})
        runtime.update(changes)
        payload[RUNTIME_KEY] = runtime
        job.payload = payload
        context.job.payload = dict(payload)
        db.commit()
    return runtime


def _persist_result(context, result):
    with session() as db:
        job = db.get(Job, context.job.id)
        if job is None:
            return
        payload = dict(job.payload or {})
        payload[RESULT_KEY] = dict(result)
        job.payload = payload
        context.job.payload = dict(payload)
        db.commit()


def _status_or_none(provider, node, vm_id):
    try:
        value = provider.vm_status(node, int(vm_id)) or {}
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise ExecutionFailed('Nie udało się odczytać stanu VM w Proxmox') from None
    except Exception:
        raise ExecutionFailed('Nie udało się odczytać stanu VM w Proxmox') from None
    return value if isinstance(value, dict) else {}


def _task_status(provider, node, upid):
    try:
        value = provider.task_status(node, upid) or {}
    except Exception:
        raise ExecutionFailed('Nie udało się odczytać statusu taska Proxmox') from None
    return value if isinstance(value, dict) else {}


def _try_stop_task(context, provider, node, upid):
    if not isinstance(upid, str) or not upid:
        return
    try:
        provider.stop_task(node, upid)
        context.log('proxmox.clone_template.task.cancel_requested: ' + upid)
    except Exception:
        context.log('proxmox.clone_template.task.cancel_failed: ' + upid)


def _wait_task(context, provider, node, upid, label):
    if not isinstance(upid, str) or not upid:
        return
    try:
        while True:
            context.check()
            status = _task_status(provider, node, upid)
            stopped = str(status.get('status') or '').lower() == 'stopped' or bool(status.get('exitstatus'))
            if stopped:
                exit_status = str(status.get('exitstatus') or '')
                if exit_status != 'OK':
                    raise ExecutionFailed(
                        f'{label} zakończył się błędem Proxmox: ' + (exit_status or 'nieznany status')
                    )
                return
            time.sleep(POLL_SECONDS)
    except Cancelled:
        _try_stop_task(context, provider, node, upid)
        raise


def _wait_for_target(context, provider, node, vm_id, *, template=None, timeout=120):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        context.check()
        last = _status_or_none(provider, node, vm_id)
        if last is not None:
            if template is None or int(last.get('template') or 0) == int(template):
                return last
        time.sleep(POLL_SECONDS)
    if template == 1:
        raise ExecutionFailed(f'VMID {vm_id} nie został potwierdzony jako template w Proxmox')
    raise ExecutionFailed(f'VMID {vm_id} nie pojawił się w Proxmox po zakończeniu klonowania')


def _load_provider(context):
    payload = context.job.payload or {}
    try:
        provider_id = int(payload['provider_id'])
    except (KeyError, TypeError, ValueError):
        raise ExecutionFailed('Clone-to-template job has invalid provider_id') from None
    with session() as db:
        provider_row = db.get(Provider, provider_id)
        if provider_row is None or provider_row.type != 'proxmox':
            raise ExecutionFailed('Provider Proxmox nie istnieje lub zmienił typ')
        credential = db.get(Credential, provider_row.credentials_id)
        if credential is None or credential.type != 'proxmox':
            raise ExecutionFailed('Credential Proxmox nie istnieje lub jest nieprawidłowy')
        if credential.expires_at is not None:
            from app.models import now
            if credential.expires_at <= now():
                raise ExecutionFailed('Credential Proxmox wygasł przed uruchomieniem zadania')
        db.expunge(credential)
    return provider_for(credential)


def _validate_payload(context):
    payload = context.job.payload or {}
    try:
        source_node = str(payload['source_node'])
        source_vm_id = int(payload['source_vm_id'])
        target_node = str(payload['target_node'])
        target_vm_id = int(payload['target_vm_id'])
        name = str(payload['name']).strip()
    except (KeyError, TypeError, ValueError):
        raise ExecutionFailed('Clone-to-template job payload is incomplete') from None
    if source_vm_id < 100 or target_vm_id < 100 or source_vm_id == target_vm_id:
        raise ExecutionFailed('Nieprawidłowy źródłowy lub docelowy VMID')
    if not source_node or not target_node or not name:
        raise ExecutionFailed('Brak node lub nazwy docelowego template')
    return {
        'provider_id': int(payload['provider_id']),
        'source_node': source_node,
        'source_vm_id': source_vm_id,
        'target_node': target_node,
        'target_vm_id': target_vm_id,
        'name': name,
        'storage': payload.get('storage') or None,
        'pool': payload.get('pool') or None,
    }


def execute(context):
    values = _validate_payload(context)
    provider = _load_provider(context)
    source_node = values['source_node']
    source_vm_id = values['source_vm_id']
    target_node = values['target_node']
    target_vm_id = values['target_vm_id']
    target_name = values['name']

    context.stage('proxmox.clone_template.validating')
    context.log(
        f'proxmox.clone_template.request: source={source_node}/{source_vm_id} '
        f'target={target_node}/{target_vm_id} name={target_name}'
    )

    source = _status_or_none(provider, source_node, source_vm_id)
    if source is None:
        raise ExecutionFailed(f'Źródłowa VMID {source_vm_id} nie istnieje w Proxmox')
    if int(source.get('template') or 0) == 1:
        raise ExecutionFailed('Źródłem operacji musi być VM, a nie template')

    runtime = _runtime(context)
    target = _status_or_none(provider, target_node, target_vm_id)

    if target is not None and not runtime.get('clone_requested'):
        raise ExecutionFailed(f'Docelowy VMID {target_vm_id} jest już zajęty w Proxmox')

    if target is not None and str(target.get('name') or '') != target_name:
        raise ExecutionFailed(
            f'Docelowy VMID {target_vm_id} istnieje, ale ma inną nazwę; '
            'zadanie nie przejmie obcego zasobu'
        )

    clone_task = runtime.get('clone_task')
    if target is None and clone_task:
        status = _task_status(provider, source_node, clone_task)
        if str(status.get('status') or '').lower() != 'stopped':
            context.stage('proxmox.clone_template.waiting_clone')
            _wait_task(context, provider, source_node, clone_task, 'Klonowanie VM')
            target = _wait_for_target(context, provider, target_node, target_vm_id)
        elif str(status.get('exitstatus') or '') == 'OK':
            target = _wait_for_target(context, provider, target_node, target_vm_id)
        else:
            clone_task = None

    if target is None:
        context.stage('proxmox.clone_template.cloning')
        _persist(context, clone_requested=True)
        try:
            clone_task = provider.clone_vm(
                source_node,
                source_vm_id,
                new_vm_id=target_vm_id,
                name=target_name,
                target=target_node,
                full=True,
                storage=values['storage'],
                pool=values['pool'],
            )
        except HTTPException as exc:
            raise ExecutionFailed('Nie udało się uruchomić klonowania VM w Proxmox') from None
        except Exception:
            raise ExecutionFailed('Nie udało się uruchomić klonowania VM w Proxmox') from None
        _persist(context, clone_task=clone_task, clone_submitted=True)
        context.log('proxmox.clone_template.clone.started: ' + str(clone_task or 'synchronous'))
        context.stage('proxmox.clone_template.waiting_clone')
        _wait_task(context, provider, source_node, clone_task, 'Klonowanie VM')
        target = _wait_for_target(context, provider, target_node, target_vm_id)

    _persist(context, clone_completed=True)
    context.log(f'proxmox.clone_template.clone.completed: {target_node}/{target_vm_id}')

    if int(target.get('template') or 0) != 1:
        runtime = _runtime(context)
        template_task = runtime.get('template_task')
        if template_task:
            status = _task_status(provider, target_node, template_task)
            stopped = str(status.get('status') or '').lower() == 'stopped' or bool(status.get('exitstatus'))
            if not stopped:
                context.stage('proxmox.clone_template.waiting_template')
                _wait_task(context, provider, target_node, template_task, 'Konwersja do template')
            elif str(status.get('exitstatus') or '') != 'OK':
                template_task = None

        target = _status_or_none(provider, target_node, target_vm_id)
        if target is None:
            raise ExecutionFailed(f'Sklonowany VMID {target_vm_id} zniknął przed konwersją do template')

        if int(target.get('template') or 0) != 1 and not template_task:
            context.stage('proxmox.clone_template.converting')
            _persist(context, template_requested=True)
            try:
                template_task = provider.convert_to_template(target_node, target_vm_id)
            except HTTPException:
                raise ExecutionFailed('Nie udało się uruchomić konwersji klona do template') from None
            except Exception:
                raise ExecutionFailed('Nie udało się uruchomić konwersji klona do template') from None
            _persist(context, template_task=template_task, template_submitted=True)
            context.log('proxmox.clone_template.template.started: ' + str(template_task or 'synchronous'))
            context.stage('proxmox.clone_template.waiting_template')
            _wait_task(context, provider, target_node, template_task, 'Konwersja do template')

    context.stage('proxmox.clone_template.verifying')
    target = _wait_for_target(context, provider, target_node, target_vm_id, template=1)
    source = _status_or_none(provider, source_node, source_vm_id)
    if source is None:
        raise ExecutionFailed('Template utworzono, ale źródłowa VM zniknęła; wymagana jest weryfikacja')
    if int(source.get('template') or 0) == 1:
        raise ExecutionFailed('Źródłowa VM została nieoczekiwanie oznaczona jako template')

    with session() as db:
        target_inventory = db.scalar(select(ManagedVM).where(
            ManagedVM.provider_id == values['provider_id'],
            ManagedVM.vm_id == target_vm_id,
        ))
        if target_inventory is not None and target_inventory.deployment_id is None:
            db.delete(target_inventory)
            db.commit()

    result = {
        'provider_id': values['provider_id'],
        'source_node': source_node,
        'source_vm_id': source_vm_id,
        'target_node': target_node,
        'target_vm_id': target_vm_id,
        'name': str(target.get('name') or target_name),
        'template': True,
        'source_preserved': True,
    }
    _persist(context, verified=True)
    _persist_result(context, result)
    context.stage('proxmox.clone_template.completed')
    context.log(
        f'proxmox.clone_template.completed: template={target_node}/{target_vm_id}; '
        f'source_preserved={source_node}/{source_vm_id}'
    )
    return result

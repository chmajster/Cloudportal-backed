from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path

from fastapi import HTTPException

from app.api.schemas import BlueprintInput
from app.config import settings


MAX_OVF_BYTES = 4 * 1024 * 1024
MAX_DISKS = 8
MAX_VIRTUAL_DISK_BYTES = 64 * 1024 ** 4
SAFE_ARCHIVE_NAME = re.compile(r'^[^\x00]+$')


def staging_root() -> Path:
    root = settings().data_dir / 'appliance-imports'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    return root


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _safe_archive_member(member: tarfile.TarInfo) -> bool:
    name = member.name.replace('\\', '/')
    parts = [part for part in name.split('/') if part not in {'', '.'}]
    safe_path = (
        bool(name)
        and SAFE_ARCHIVE_NAME.fullmatch(name) is not None
        and not name.startswith('/')
        and '..' not in parts
    )
    return safe_path and (member.isfile() or member.isdir()) and not member.issym() and not member.islnk()


def _local_name(value: str) -> str:
    return value.rsplit('}', 1)[-1]


def _attr(element, name: str):
    for key, value in element.attrib.items():
        if _local_name(key) == name:
            return value
    return None


def _ovf_metadata(raw: bytes) -> dict:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise HTTPException(422, 'Plik OVF wewnątrz OVA jest nieprawidłowym XML') from exc

    cpu = None
    memory_mib = None
    networks = 0
    appliance_name = None

    virtual_system = next((item for item in root.iter() if _local_name(item.tag) == 'VirtualSystem'), None)
    if virtual_system is not None:
        appliance_name = _attr(virtual_system, 'id')
        for child in virtual_system:
            if _local_name(child.tag) == 'Name' and (child.text or '').strip():
                appliance_name = child.text.strip()
                break

    for item in root.iter():
        if _local_name(item.tag) != 'Item':
            continue
        values = {
            _local_name(child.tag): (child.text or '').strip()
            for child in item
        }
        resource_type = values.get('ResourceType')
        quantity = values.get('VirtualQuantity')
        if resource_type == '3' and quantity:
            try:
                cpu = max(1, min(128, int(quantity)))
            except ValueError:
                pass
        elif resource_type == '4' and quantity:
            try:
                raw_memory = int(quantity)
            except ValueError:
                continue
            units = (values.get('AllocationUnits') or '').lower()
            if '2^30' in units or 'gigabyte' in units:
                memory_mib = raw_memory * 1024
            elif '2^10' in units or 'kilobyte' in units:
                memory_mib = max(1, raw_memory // 1024)
            else:
                memory_mib = raw_memory
            memory_mib = max(128, min(1048576, memory_mib))
        elif resource_type == '10':
            networks += 1

    files = {}
    for item in root.iter():
        if _local_name(item.tag) == 'File':
            file_id = _attr(item, 'id')
            href = _attr(item, 'href')
            if file_id and href:
                files[file_id] = href.replace('\\', '/')

    disk_hrefs = []
    for item in root.iter():
        if _local_name(item.tag) != 'Disk':
            continue
        file_ref = _attr(item, 'fileRef')
        href = files.get(file_ref)
        if href and href.lower().endswith('.vmdk') and href not in disk_hrefs:
            disk_hrefs.append(href)

    return {
        'name': appliance_name,
        'cpu': cpu or 2,
        'memory': memory_mib or 4096,
        'networks': networks,
        'disk_hrefs': disk_hrefs,
    }


def inspect_ova(path: Path) -> dict:
    try:
        archive = tarfile.open(path, mode='r:*')
    except (tarfile.TarError, OSError) as exc:
        raise HTTPException(422, 'Plik nie jest poprawnym archiwum OVA') from exc

    with archive:
        members = archive.getmembers()
        if not members or len(members) > 4096:
            raise HTTPException(422, 'OVA ma nieprawidłową liczbę elementów')
        unsafe = [member.name for member in members if not _safe_archive_member(member)]
        if unsafe:
            raise HTTPException(422, 'OVA zawiera niedozwolony typ lub ścieżkę elementu')

        ovf_members = [member for member in members if member.name.lower().endswith('.ovf')]
        vmdk_members = [member for member in members if member.name.lower().endswith('.vmdk')]
        if len(ovf_members) != 1:
            raise HTTPException(422, 'OVA musi zawierać dokładnie jeden plik OVF')
        if not (1 <= len(vmdk_members) <= MAX_DISKS):
            raise HTTPException(422, f'OVA musi zawierać od 1 do {MAX_DISKS} dysków VMDK')
        if ovf_members[0].size > MAX_OVF_BYTES:
            raise HTTPException(422, 'Plik OVF w archiwum jest zbyt duży')

        stream = archive.extractfile(ovf_members[0])
        if stream is None:
            raise HTTPException(422, 'Nie można odczytać pliku OVF z OVA')
        metadata = _ovf_metadata(stream.read(MAX_OVF_BYTES + 1))

        by_name = {member.name.replace('\\', '/'): member for member in vmdk_members}
        ordered = []
        for href in metadata['disk_hrefs']:
            member = by_name.get(href)
            if member is None:
                basename = Path(href).name
                candidates = [value for name, value in by_name.items() if Path(name).name == basename]
                member = candidates[0] if len(candidates) == 1 else None
            if member is not None and member not in ordered:
                ordered.append(member)
        ordered.extend(member for member in vmdk_members if member not in ordered)

        return {
            **metadata,
            'ovf_name': ovf_members[0].name,
            'disk_members': [member.name for member in ordered],
            'archive_size': path.stat().st_size,
        }


def _qemu_img_info(path: Path) -> dict:
    try:
        result = subprocess.run(
            ['qemu-img', 'info', '--output=json', str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        data = json.loads(result.stdout)
    except FileNotFoundError:
        raise HTTPException(503, 'Brak qemu-img. Zainstaluj qemu-utils/qemu-img i uruchom usługę ponownie') from None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError):
        raise HTTPException(422, 'Nie można zweryfikować dysku VMDK z OVA') from None

    image_format = str(data.get('format') or '')
    if image_format not in {'vmdk', 'qcow2', 'raw'}:
        raise HTTPException(422, f'Nieobsługiwany format dysku w OVA: {image_format or "unknown"}')
    if data.get('backing-filename') or data.get('full-backing-filename'):
        raise HTTPException(422, 'Dysk OVA z zewnętrznym backing file nie jest obsługiwany')
    virtual_size = int(data.get('virtual-size') or 0)
    if virtual_size <= 0 or virtual_size > MAX_VIRTUAL_DISK_BYTES:
        raise HTTPException(422, 'Wirtualny rozmiar dysku OVA jest nieprawidłowy lub zbyt duży')
    return {'format': image_format, 'virtual_size': virtual_size}


def convert_ova_disks(ova_path: Path, workdir: Path) -> dict:
    metadata = inspect_ova(ova_path)
    workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(workdir, 0o700)

    converted = []
    with tarfile.open(ova_path, mode='r:*') as archive:
        members = {member.name: member for member in archive.getmembers()}
        for index, member_name in enumerate(metadata['disk_members']):
            member = members[member_name]
            source = workdir / f'source-{index}.vmdk'
            stream = archive.extractfile(member)
            if stream is None:
                raise HTTPException(422, f'Nie można odczytać dysku {index + 1} z OVA')
            with source.open('xb') as destination:
                shutil.copyfileobj(stream, destination, length=1024 * 1024)
            os.chmod(source, 0o600)

            info = _qemu_img_info(source)
            free = shutil.disk_usage(workdir).free
            if free < max(1024 ** 3, source.stat().st_size + 512 * 1024 ** 2):
                raise HTTPException(507, 'Za mało miejsca tymczasowego na konwersję OVA')

            target = workdir / f'disk-{index}.qcow2'
            try:
                subprocess.run(
                    ['qemu-img', 'convert', '-f', info['format'], '-O', 'qcow2', str(source), str(target)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=7200,
                )
            except subprocess.TimeoutExpired:
                raise HTTPException(504, f'Konwersja dysku {index + 1} OVA przekroczyła limit 2 godzin') from None
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or '').strip()[-1000:]
                raise HTTPException(422, 'Konwersja dysku OVA nie powiodła się' + (': ' + detail if detail else '')) from None
            finally:
                source.unlink(missing_ok=True)

            os.chmod(target, 0o600)
            converted.append({
                'path': target,
                'virtual_size': info['virtual_size'],
            })

    return {
        **metadata,
        'sha256': sha256_file(ova_path),
        'disks': converted,
    }


def appliance_blueprint_input(
    *,
    slug: str,
    name: str,
    provider_id: int,
    credentials_id: int,
    node: str,
    storage: str,
    network: str,
    import_file_ids: list[str],
    cpu: int,
    memory: int,
    disk_bus: str,
    vlan_id: int | None,
    source_sha256: str,
    source_name: str,
) -> BlueprintInput:
    variables = {
        'name': '{{ vm_name }}',
        'node': node,
        'storage': storage,
        'network': network,
        'vlan_id': vlan_id,
        'cpu': '{{ cpu }}',
        'memory': '{{ memory }}',
        'import_file_ids': import_file_ids,
        'disk_bus': disk_bus,
        'started': True,
        'qemu_guest_agent': False,
        'tags': ['appliance', 'ova'],
    }
    description = (
        f'Appliance utworzony z OVA: {source_name}. '
        f'SHA-256: {source_sha256}. Źródłowe dyski są przechowywane w Proxmox jako content Import.'
    )
    return BlueprintInput.model_validate({
        'slug': slug,
        'name': name,
        'description': description,
        'is_active': True,
        'visibility': {'backend': True, 'cloudportal': True, 'api': True},
        'variables_schema': {
            'vm_name': {
                'type': 'string',
                'label': 'Nazwa VM',
                'required': True,
            },
            'cpu': {
                'type': 'integer',
                'label': 'vCPU',
                'required': True,
                'default': int(cpu),
                'min': 1,
                'max': 128,
            },
            'memory': {
                'type': 'integer',
                'label': 'RAM (MiB)',
                'required': True,
                'default': int(memory),
                'min': 128,
                'max': 1048576,
            },
        },
        'deployment': {
            'name': '{{ vm_name }}',
            'provider_id': provider_id,
            'credentials_id': credentials_id,
            'template': 'proxmox-appliance',
            'variables': variables,
            'executor': 'terraform',
            'select_apmid_on_execute': False,
            'select_environment_on_execute': False,
        },
        'workflow': [
            {
                'id': 'apply',
                'type': 'terraform_apply',
                'depends_on': [],
                'retry': 0,
                'timeout': 7200,
            },
        ],
        'requires_approval': False,
        'recovery_policy': 'preserve',
    })

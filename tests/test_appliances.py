import io
import tarfile
import uuid
from pathlib import Path

import pytest

from app.appliances import service


OVF = b'''<?xml version="1.0" encoding="UTF-8"?>
<Envelope xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1"
          xmlns:rasd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData">
  <References>
    <File ovf:id="file1" ovf:href="disk1.vmdk"/>
  </References>
  <DiskSection>
    <Disk ovf:diskId="disk1" ovf:fileRef="file1"/>
  </DiskSection>
  <VirtualSystem ovf:id="router-appliance">
    <Name>Router Appliance</Name>
    <VirtualHardwareSection>
      <Item><rasd:ResourceType>3</rasd:ResourceType><rasd:VirtualQuantity>4</rasd:VirtualQuantity></Item>
      <Item><rasd:ResourceType>4</rasd:ResourceType><rasd:VirtualQuantity>8192</rasd:VirtualQuantity><rasd:AllocationUnits>byte * 2^20</rasd:AllocationUnits></Item>
      <Item><rasd:ResourceType>10</rasd:ResourceType></Item>
    </VirtualHardwareSection>
  </VirtualSystem>
</Envelope>
'''


def write_ova(path: Path, *, traversal=False):
    with tarfile.open(path, 'w') as archive:
        directory = tarfile.TarInfo('payload/')
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o755
        archive.addfile(directory)

        ovf_info = tarfile.TarInfo('payload/appliance.ovf')
        ovf_info.size = len(OVF)
        archive.addfile(ovf_info, io.BytesIO(OVF))

        disk_name = '../disk1.vmdk' if traversal else 'payload/disk1.vmdk'
        disk = b'fake-vmdk-data'
        disk_info = tarfile.TarInfo(disk_name)
        disk_info.size = len(disk)
        archive.addfile(disk_info, io.BytesIO(disk))


def test_ova_inspection_accepts_safe_directories_and_reads_hardware(tmp_path):
    ova = tmp_path / 'router.ova'
    write_ova(ova)

    result = service.inspect_ova(ova)

    assert result['name'] == 'Router Appliance'
    assert result['cpu'] == 4
    assert result['memory'] == 8192
    assert result['networks'] == 1
    assert result['disk_members'] == ['payload/disk1.vmdk']


def test_ova_inspection_rejects_path_traversal(tmp_path):
    ova = tmp_path / 'unsafe.ova'
    write_ova(ova, traversal=True)

    with pytest.raises(Exception, match='niedozwolony'):
        service.inspect_ova(ova)


def test_ova_conversion_uses_controlled_qcow2_output(tmp_path, monkeypatch):
    ova = tmp_path / 'router.ova'
    write_ova(ova)

    monkeypatch.setattr(service, '_qemu_img_info', lambda path: {
        'format': 'vmdk',
        'virtual_size': 40 * 1024 ** 3,
    })

    def fake_run(argv, **kwargs):
        assert argv[:5] == ['qemu-img', 'convert', '-f', 'vmdk', '-O']
        target = Path(argv[-1])
        target.write_bytes(b'qcow2')
        return type('Result', (), {'stdout': '', 'stderr': '', 'returncode': 0})()

    monkeypatch.setattr(service.subprocess, 'run', fake_run)

    result = service.convert_ova_disks(ova, tmp_path / 'work')

    assert result['sha256'] == service.sha256_file(ova)
    assert len(result['disks']) == 1
    assert result['disks'][0]['path'].read_bytes() == b'qcow2'
    assert not (tmp_path / 'work' / 'source-0.vmdk').exists()


def test_ova_upload_creates_executable_appliance_blueprint(client, headers, monkeypatch, tmp_path):
    credential = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'PVE appliance',
        'type': 'proxmox',
        'endpoint': 'https://pve.example.com:8006',
        'username': 'root@pam',
        'secrets': {
            'token_id': 'root@pam!cloudportal',
            'token_secret': 'secret-value',
        },
    })
    assert credential.status_code == 201, credential.text

    provider = client.post('/api/v1/providers', headers=headers, json={
        'name': 'PVE',
        'type': 'proxmox',
        'credentials_id': credential.json()['id'],
    })
    assert provider.status_code == 201, provider.text
    provider_id = provider.json()['id']

    def discover(_self, resource, node=None):
        if resource == 'nodes':
            return [{'node': 'pve01'}]
        if resource == 'storages':
            assert node == 'pve01'
            return [
                {'storage': 'local', 'content': 'iso,backup,import', 'type': 'dir'},
                {'storage': 'local-lvm', 'content': 'images,rootdir', 'type': 'lvmthin'},
            ]
        if resource == 'networks':
            assert node == 'pve01'
            return [{'iface': 'vmbr0', 'type': 'bridge'}]
        raise AssertionError(resource)

    monkeypatch.setattr('app.appliances.api.ProxmoxProvider.discover', discover)

    def fake_convert(_ova_path, workdir):
        workdir.mkdir(parents=True, exist_ok=True)
        disk = workdir / 'disk-0.qcow2'
        disk.write_bytes(b'qcow2')
        return {
            'sha256': 'a' * 64,
            'cpu': 4,
            'memory': 8192,
            'disks': [{'path': disk, 'virtual_size': 40 * 1024 ** 3}],
        }

    monkeypatch.setattr('app.appliances.api.convert_ova_disks', fake_convert)

    uploads = []

    def upload(_self, node, storage, path, filename):
        assert node == 'pve01'
        assert storage == 'local'
        assert Path(path).is_file()
        uploads.append(filename)
        return f'local:import/{filename}'

    monkeypatch.setattr('app.appliances.api.ProxmoxProvider.upload_import_image', upload)
    monkeypatch.setattr('app.appliances.api.ProxmoxProvider.delete_storage_volume', lambda *args, **kwargs: None)

    payload = b'x' * (2 * 1024 * 1024)
    response = client.post(
        '/api/v1/appliances/ova-blueprints',
        headers=headers,
        params={
            'provider_id': provider_id,
            'node': 'pve01',
            'import_storage': 'local',
            'storage': 'local-lvm',
            'network': 'vmbr0',
            'slug': 'router-appliance',
            'name': 'Router Appliance',
            'disk_bus': 'scsi',
        },
        files={'file': ('router.ova', payload, 'application/octet-stream')},
    )
    assert response.status_code == 201, response.text
    blueprint = response.json()
    assert blueprint['deployment']['template'] == 'proxmox-appliance'
    assert blueprint['deployment']['variables']['import_file_ids'] == [
        'local:import/router-appliance-aaaaaaaaaaaa-disk1.qcow2'
    ]
    assert uploads == ['router-appliance-aaaaaaaaaaaa-disk1.qcow2']

    launched = client.post(
        f"/api/v1/blueprints/{blueprint['id']}/execute",
        headers={**headers, 'Idempotency-Key': str(uuid.uuid4())},
        json={'variables': {'vm_name': 'router01', 'cpu': 6, 'memory': 12288}},
    )
    assert launched.status_code == 202, launched.text
    deployment = launched.json()
    assert deployment['template'] == 'proxmox-appliance'
    assert deployment['variables']['name'] == 'router01'
    assert deployment['variables']['cpu'] == 6
    assert deployment['variables']['memory'] == 12288
    assert deployment['variables']['import_file_ids'] == [
        'local:import/router-appliance-aaaaaaaaaaaa-disk1.qcow2'
    ]


def test_ova_upload_requires_proxmox_import_storage(client, headers, monkeypatch):
    credential = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'PVE no import',
        'type': 'proxmox',
        'endpoint': 'https://pve.example.com:8006',
        'username': 'root@pam',
        'secrets': {
            'token_id': 'root@pam!cloudportal',
            'token_secret': 'secret-value',
        },
    }).json()
    provider = client.post('/api/v1/providers', headers=headers, json={
        'name': 'PVE no import',
        'type': 'proxmox',
        'credentials_id': credential['id'],
    }).json()

    def discover(_self, resource, node=None):
        if resource == 'nodes':
            return [{'node': 'pve01'}]
        if resource == 'storages':
            return [{'storage': 'local-lvm', 'content': 'images,rootdir', 'type': 'lvmthin'}]
        if resource == 'networks':
            return [{'iface': 'vmbr0', 'type': 'bridge'}]
        raise AssertionError(resource)

    monkeypatch.setattr('app.appliances.api.ProxmoxProvider.discover', discover)

    response = client.post(
        '/api/v1/appliances/ova-blueprints',
        headers=headers,
        params={
            'provider_id': provider['id'],
            'node': 'pve01',
            'import_storage': 'local-lvm',
            'storage': 'local-lvm',
            'network': 'vmbr0',
            'slug': 'bad-appliance',
            'name': 'Bad Appliance',
        },
        files={'file': ('bad.ova', b'not-used', 'application/octet-stream')},
    )
    assert response.status_code == 422
    assert 'Content: Import' in response.text or 'content typu Import' in response.text

import pytest
from pydantic import ValidationError

from app.api.schemas import BlueprintDeployment, BlueprintInput


def test_blueprint_deployment_runtime_classification_fields_are_valid():
    deployment = BlueprintDeployment(
        name='vm-test',
        provider_id=1,
        credentials_id=1,
        variables={},
        apmid='LEO',
        environment='dev',
        select_apmid_on_execute=True,
        select_environment_on_execute=True,
    )

    assert deployment.apmid == 'LEO'
    assert deployment.environment == 'dev'
    assert deployment.select_apmid_on_execute is True
    assert deployment.select_environment_on_execute is True


def test_blueprint_deployment_rejects_invalid_apmid_and_environment():
    with pytest.raises(ValidationError):
        BlueprintDeployment(
            name='vm-test',
            provider_id=1,
            credentials_id=1,
            variables={},
            apmid='bad value!',
        )

    with pytest.raises(ValidationError):
        BlueprintDeployment(
            name='vm-test',
            provider_id=1,
            credentials_id=1,
            variables={},
            environment='staging',
        )


def _direct_blueprint(workflow):
    return BlueprintInput(
        slug='direct-proxmox',
        name='Direct Proxmox',
        deployment=BlueprintDeployment(
            name='direct-proxmox',
            provider_id=1,
            credentials_id=1,
            template='proxmox-vm',
            executor='proxmox',
            variables={'node': 'pve01', 'template_id': 9000},
        ),
        workflow=workflow,
    )


def test_direct_proxmox_rejects_unimplemented_create_vm():
    with pytest.raises(ValidationError, match='create_vm'):
        _direct_blueprint([
            {'id': 'create', 'type': 'create_vm'},
        ])


def test_direct_proxmox_allows_destroy_only_as_rollback_target():
    blueprint = _direct_blueprint([
        {'id': 'destroy', 'type': 'terraform_destroy'},
        {'id': 'clone', 'type': 'clone_vm'},
        {
            'id': 'health',
            'type': 'health_check',
            'depends_on': ['clone'],
            'rollback': 'destroy',
        },
    ])
    assert blueprint.deployment.executor == 'proxmox'
    assert blueprint.workflow[-1].rollback == 'destroy'


def test_direct_proxmox_requires_cloud_init_before_start():
    with pytest.raises(ValidationError, match='start_vm'):
        _direct_blueprint([
            {'id': 'clone', 'type': 'clone_vm'},
            {'id': 'start', 'type': 'start_vm', 'depends_on': ['clone']},
            {'id': 'cloud', 'type': 'cloud_init', 'depends_on': ['clone']},
        ])

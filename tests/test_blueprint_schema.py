import pytest
from pydantic import ValidationError

from app.api.schemas import BlueprintDeployment


def test_blueprint_deployment_runtime_classification_fields_are_valid():
    deployment = BlueprintDeployment(
        name='vm-test',
        provider_id=1,
        credentials_id=1,
        variables={},
        select_apmid_on_execute=True,
        select_environment_on_execute=True,
    )

    assert deployment.apmid is None
    assert deployment.environment is None
    assert deployment.select_apmid_on_execute is True
    assert deployment.select_environment_on_execute is True


def test_blueprint_deployment_rejects_fixed_values_when_runtime_selection_is_enabled():
    with pytest.raises(ValidationError):
        BlueprintDeployment(
            name='vm-test',
            provider_id=1,
            credentials_id=1,
            variables={},
            apmid='LEO',
            select_apmid_on_execute=True,
        )

    with pytest.raises(ValidationError):
        BlueprintDeployment(
            name='vm-test',
            provider_id=1,
            credentials_id=1,
            variables={},
            environment='dev',
            select_environment_on_execute=True,
        )


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

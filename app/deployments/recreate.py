from fastapi import HTTPException

from app.catalog import template_definition


def recreate_resource_address(template_id: str) -> str:
    """Return the only Terraform resource address approved for full recreation."""
    definition, _ = template_definition(template_id)
    resource_address = ((definition.get('import') or {}).get('resource_address') or '').strip()
    if not resource_address:
        raise HTTPException(422, 'Terraform template does not support full resource recreation')
    return resource_address


def recreate_job_payload(deployment) -> dict:
    """Build a normal apply payload while marking it as an explicit replacement."""
    recreate_resource_address(deployment.template)
    payload = dict(deployment.workflow or {})
    payload['_recreate'] = True
    return payload

from dataclasses import dataclass, field

from fastapi import HTTPException


@dataclass(slots=True)
class Day2Failure(Exception):
    code: str
    message: str
    status_code: int = 409
    details: dict = field(default_factory=dict)

    def as_detail(self, correlation_id: str | None = None) -> dict:
        result = {
            'code': self.code,
            'message': self.message,
            'details': self.details,
        }
        if correlation_id:
            result['correlation_id'] = correlation_id
        return result

    def http(self, correlation_id: str | None = None) -> HTTPException:
        return HTTPException(self.status_code, self.as_detail(correlation_id))


ERROR_MESSAGES = {
    'PROVIDER_UNAVAILABLE': 'Provider is unavailable',
    'RESOURCE_NOT_FOUND': 'Resource was not found',
    'ACTION_NOT_SUPPORTED': 'Action is not supported for this resource',
    'INVALID_STATE': 'Resource is in a state that does not allow this action',
    'PERMISSION_DENIED': 'Permission denied',
    'APPROVAL_REQUIRED': 'Approval is required',
    'RESOURCE_LOCKED': 'Resource has another Day-2 action in progress',
    'QUOTA_EXCEEDED': 'Requested change exceeds quota',
    'VALIDATION_FAILED': 'Action validation failed',
    'TIMEOUT': 'Action timed out',
    'PROVIDER_ERROR': 'Provider operation failed',
    'PROTECTED_RESOURCE': 'Resource protection blocks this action',
    'TERRAFORM_OWNED': 'Terraform-managed resource cannot be changed directly by current policy',
}


def failure(code: str, *, message: str | None = None, status_code: int = 409, details: dict | None = None):
    return Day2Failure(
        code=code,
        message=message or ERROR_MESSAGES.get(code, code),
        status_code=status_code,
        details=details or {},
    )

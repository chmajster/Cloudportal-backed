"""Enterprise policy engine for CloudPortal.

RBAC answers whether an identity owns an action permission.  This package
answers whether that permitted action is acceptable for the concrete request,
resource and runtime context.
"""

from app.policy_engine.engine import evaluate

__all__ = ["evaluate"]

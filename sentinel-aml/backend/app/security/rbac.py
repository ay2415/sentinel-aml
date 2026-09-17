"""Role-based access control: permissions, document clearance and approval authority."""
from __future__ import annotations

from enum import Enum

from app.database.models import RecommendedAction, Role


class Permission(str, Enum):
    READ_ALERTS = "alerts:read"
    START_INVESTIGATION = "investigations:start"
    READ_INVESTIGATIONS = "investigations:read"
    DECIDE_RECOMMENDATION = "recommendations:decide"
    SEARCH_KB = "kb:search"
    UPLOAD_KB = "kb:upload"
    APPROVE_KB = "kb:approve"
    INGEST_DATA = "data:ingest"
    READ_AUDIT = "audit:read"
    READ_OPS = "ops:read"
    VIEW_PII = "pii:view"


ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.analyst: {Permission.READ_ALERTS, Permission.START_INVESTIGATION, Permission.READ_INVESTIGATIONS,
                   Permission.SEARCH_KB},
    Role.investigator: {Permission.READ_ALERTS, Permission.START_INVESTIGATION, Permission.READ_INVESTIGATIONS,
                        Permission.DECIDE_RECOMMENDATION, Permission.SEARCH_KB, Permission.VIEW_PII},
    Role.mlro: {Permission.READ_ALERTS, Permission.START_INVESTIGATION, Permission.READ_INVESTIGATIONS,
                Permission.DECIDE_RECOMMENDATION, Permission.SEARCH_KB, Permission.APPROVE_KB,
                Permission.READ_AUDIT, Permission.READ_OPS, Permission.VIEW_PII},
    Role.auditor: {Permission.READ_ALERTS, Permission.READ_INVESTIGATIONS, Permission.READ_AUDIT, Permission.READ_OPS},
    Role.admin: {Permission.INGEST_DATA, Permission.UPLOAD_KB, Permission.APPROVE_KB, Permission.READ_OPS,
                 Permission.READ_AUDIT, Permission.SEARCH_KB},
}

# Document classification ladder. A user sees chunks with clearance_level <= their clearance.
CLASSIFICATION_LEVELS = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
ROLE_CLEARANCE: dict[Role, int] = {
    Role.analyst: 1, Role.auditor: 1, Role.investigator: 2, Role.mlro: 3, Role.admin: 3,
}

# Who may approve each action (human-in-the-loop authority matrix).
APPROVAL_AUTHORITY: dict[RecommendedAction, set[Role]] = {
    RecommendedAction.CLOSE_FALSE_POSITIVE: {Role.investigator, Role.mlro},
    RecommendedAction.ENHANCED_MONITORING: {Role.investigator, Role.mlro},
    RecommendedAction.ESCALATE_STR: {Role.mlro},
    RecommendedAction.RESTRICT_ACCOUNT: {Role.mlro},
}


def has_permission(role: Role, perm: Permission) -> bool:
    return perm in ROLE_PERMISSIONS.get(role, set())


def clearance_for(role: Role) -> int:
    return ROLE_CLEARANCE.get(role, 0)

"""JWT authentication and FastAPI dependencies for authenticated, tenant-scoped principals."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.core.config import get_settings
from app.core.context import tenant_id_var, user_var
from app.database.models import Role
from app.security.rbac import Permission, has_permission

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    tenant_id: str
    role: Role

    @property
    def actor(self) -> str:
        return f"{self.tenant_id}/{self.username}"


def create_access_token(p: Principal) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    claims = {"sub": p.user_id, "usr": p.username, "tid": p.tenant_id, "role": p.role.value,
              "iat": now, "exp": now + timedelta(minutes=s.jwt_expiry_minutes), "iss": s.app_name}
    return jwt.encode(claims, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_token(token: str) -> Principal:
    s = get_settings()
    try:
        c = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm], issuer=s.app_name,
                       options={"require": ["exp", "sub", "tid", "role"]})
        return Principal(user_id=c["sub"], username=c["usr"], tenant_id=c["tid"], role=Role(c["role"]))
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"}) from exc


def get_principal(token: str | None = Depends(oauth2_scheme)) -> Principal:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    p = decode_token(token)
    tenant_id_var.set(p.tenant_id)
    user_var.set(p.username)
    return p


def require(perm: Permission):
    def _dep(p: Principal = Depends(get_principal)) -> Principal:
        if not has_permission(p.role, perm):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Role '{p.role.value}' lacks permission '{perm.value}'")
        return p

    return _dep

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas import LoginRequest, TokenResponse
from app.core.config import get_settings
from app.database.models import User
from app.security.auth import Principal, create_access_token, get_principal
from app.security.passwords import verify_password
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])
MAX_FAILED = 5


@router.post("/token", response_model=TokenResponse, summary="Exchange credentials for a JWT (local auth; Entra ID in production)")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.execute(select(User).where(User.tenant_id == body.tenant_id, User.username == body.username)).scalar_one_or_none()
    generic = HTTPException(401, "invalid credentials")  # no user-enumeration oracle
    if user is None:
        verify_password(body.password, "pbkdf2_sha256$600000$AAAAAAAAAAAAAAAAAAAAAA==$AAAA")  # equalise timing
        raise generic
    if not user.is_active or user.failed_logins >= MAX_FAILED:
        raise HTTPException(423, "account locked")
    if not verify_password(body.password, user.password_hash):
        user.failed_logins += 1
        audit.record(db, tenant_id=user.tenant_id, actor=f"{user.tenant_id}/{user.username}", action="login_failed", entity_type="user", entity_id=user.id)
        db.commit()
        raise generic
    user.failed_logins = 0
    p = Principal(user.id, user.username, user.tenant_id, user.role)
    audit.record(db, tenant_id=user.tenant_id, actor=p.actor, action="login_succeeded", entity_type="user", entity_id=user.id)
    db.commit()
    return TokenResponse(access_token=create_access_token(p), expires_in=get_settings().jwt_expiry_minutes * 60, role=user.role.value,
                         tenant_id=user.tenant_id)


@router.get("/me")
def me(p: Principal = Depends(get_principal)):
    return {"username": p.username, "tenant_id": p.tenant_id, "role": p.role.value}

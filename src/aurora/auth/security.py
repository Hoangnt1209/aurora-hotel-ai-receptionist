"""JWT authentication and Role-Based Access Control (RBAC) for Aurora staff endpoints."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aurora.config import get_settings
from aurora.db.models import Property, StaffUser
from aurora.db.session import get_db_session
from aurora.domain.enums import StaffRole

bearer_scheme = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class StaffIdentity(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: StaffRole
    property_id: UUID | None = None
    property_code: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int
    staff: StaffIdentity


async def authenticate_staff(
    session: AsyncSession,
    email: str,
    password: str,
) -> StaffIdentity:
    """Validate staff credentials with bcrypt and return StaffIdentity."""
    row = (
        await session.execute(
            select(StaffUser, Property.code)
            .outerjoin(Property, Property.id == StaffUser.property_id)
            .where(StaffUser.email == email.strip().lower())
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "INVALID_CREDENTIALS", "message": "Invalid email or password."},
        )

    user, prop_code = row
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "STAFF_DISABLED", "message": "Staff account is disabled."},
        )

    if not bcrypt.checkpw(password.encode("utf-8"), user.password_hash.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "INVALID_CREDENTIALS", "message": "Invalid email or password."},
        )

    return StaffIdentity(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=StaffRole(user.role),
        property_id=user.property_id,
        property_code=prop_code,
    )


def create_staff_jwt(identity: StaffIdentity) -> tuple[str, int]:
    """Sign an HS256 JWT exclusively for authenticated hotel staff."""
    settings = get_settings()
    now = datetime.now(UTC)
    ttl_seconds = settings.jwt_expire_minutes * 60
    payload = {
        "sub": str(identity.id),
        "email": identity.email,
        "full_name": identity.full_name,
        "role": identity.role.value,
        "property_id": str(identity.property_id) if identity.property_id else None,
        "property_code": identity.property_code,
        "typ": "staff_access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, ttl_seconds


async def get_current_staff(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> StaffIdentity:
    """FastAPI dependency rejecting unauthenticated, guest, or AI_AGENT callers."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "AUTH_REQUIRED", "message": "Staff bearer token is required."},
        )

    settings = get_settings()
    try:
        decoded = jwt.decode(
            credentials.credentials,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "INVALID_TOKEN", "message": "Invalid or expired bearer token."},
        ) from exc

    # Explicitly reject guest verification tokens or AI agent principals on staff routes
    if decoded.get("typ") != "staff_access":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "RBAC_FORBIDDEN",
                "message": "AI agent and guest tokens cannot access staff endpoints.",
            },
        )

    staff_id = UUID(decoded["sub"])
    row = (
        await session.execute(
            select(StaffUser, Property.code)
            .outerjoin(Property, Property.id == StaffUser.property_id)
            .where(StaffUser.id == staff_id, StaffUser.is_active.is_(True))
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "STAFF_NOT_FOUND", "message": "Staff account unavailable."},
        )
    user, prop_code = row
    return StaffIdentity(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=StaffRole(user.role),
        property_id=user.property_id,
        property_code=prop_code,
    )


def require_staff_roles(*allowed_roles: StaffRole) -> Callable[..., StaffIdentity]:
    """Dependency enforcing role membership for staff operations."""

    async def _guard(staff: StaffIdentity = Depends(get_current_staff)) -> StaffIdentity:
        if staff.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error_code": "INSUFFICIENT_ROLE",
                    "message": f"Role '{staff.role.value}' is not authorized for this action.",
                },
            )
        return staff

    return _guard


def assert_property_scope(staff: StaffIdentity, target_property_code: str | None) -> str | None:
    """Enforce property scoping for non-ADMIN staff while allowing ADMIN global view."""
    if staff.role == StaffRole.ADMIN:
        return target_property_code.strip().upper() if target_property_code else None
    if target_property_code and staff.property_code and (
        target_property_code.strip().upper() != staff.property_code
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "FORBIDDEN_PROPERTY_SCOPE",
                "message": f"Staff scoped to {staff.property_code} cannot access {target_property_code}.",
            },
        )
    return staff.property_code

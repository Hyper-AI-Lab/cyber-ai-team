from datetime import UTC, datetime, timedelta
from secrets import compare_digest, token_urlsafe
from threading import Lock

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from cyber_team.config import settings

ALGORITHM = "HS256"
bearer_scheme = HTTPBearer(auto_error=False)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_websocket_ticket_lock = Lock()


class Principal(BaseModel):
    subject: str
    email: str
    role: str
    token_type: str


_websocket_tickets: dict[str, tuple[Principal, datetime]] = {}


def verify_owner_password(password: str) -> bool:
    if settings.owner_password_hash:
        return pwd_context.verify(password, settings.owner_password_hash)
    return compare_digest(password, settings.owner_password)


def create_token(
    subject: str,
    email: str,
    role: str,
    token_type: str,
    expires_delta: timedelta,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "email": email,
        "role": role,
        "typ": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_owner_access_token() -> str:
    return create_token(
        subject="owner",
        email=settings.owner_email,
        role="owner",
        token_type="access",
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_owner_refresh_token() -> str:
    return create_token(
        subject="owner",
        email=settings.owner_email,
        role="owner",
        token_type="refresh",
        expires_delta=timedelta(days=settings.refresh_token_expire_days),
    )


def create_websocket_ticket(principal: Principal) -> tuple[str, datetime]:
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=settings.websocket_ticket_expire_seconds)
    ticket = token_urlsafe(32)
    with _websocket_ticket_lock:
        _prune_websocket_tickets(now)
        _websocket_tickets[ticket] = (principal, expires_at)
    return ticket, expires_at


def consume_websocket_ticket(ticket: str) -> Principal | None:
    now = datetime.now(UTC)
    with _websocket_ticket_lock:
        _prune_websocket_tickets(now)
        item = _websocket_tickets.pop(ticket, None)
    if not item:
        return None
    principal, expires_at = item
    if expires_at <= now:
        return None
    return principal.model_copy(update={"token_type": "websocket"})


def _prune_websocket_tickets(now: datetime) -> None:
    expired = [
        ticket
        for ticket, (_, expires_at) in _websocket_tickets.items()
        if expires_at <= now
    ]
    for ticket in expired:
        _websocket_tickets.pop(ticket, None)


def decode_token(token: str, expected_type: str | None = None) -> Principal:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    token_type = payload.get("typ")
    if expected_type and token_type != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
            headers={"WWW-Authenticate": "Bearer"},
        )

    subject = payload.get("sub")
    email = payload.get("email")
    role = payload.get("role")
    if not subject or not email or not role or not token_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token claims",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return Principal(subject=subject, email=email, role=role, token_type=token_type)


async def get_current_principal(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> Principal:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_token(credentials.credentials, expected_type="access")


async def require_owner(principal: Principal = Depends(get_current_principal)) -> Principal:
    if principal.role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner access required",
        )
    return principal

"""Bearer/JWT authentication and tenant authorization.

Every added endpoint declares HTTPBearer security; this dependency enforces it. In prod
it verifies the JWT against the configured JWKS and extracts tenant scope. For local/test
runs (auth_disabled=True) it accepts a dev token and a tenant header.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

_settings = get_settings()
_bearer = HTTPBearer(auto_error=not _settings.auth_disabled)


@dataclass
class Principal:
    subject: str
    tenant_id: str
    scopes: tuple[str, ...] = ()


async def current_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    x_tenant_id: str | None = Header(default=None),
) -> Principal:
    if _settings.auth_disabled:
        # dev/test path: trust the tenant header, synthesize a principal
        return Principal(subject="dev", tenant_id=x_tenant_id or "dev-tenant")

    if creds is None or not creds.credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")

    claims = _verify_jwt(creds.credentials)
    tenant = claims.get("tenant_id") or claims.get("tid")
    if not tenant:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "token has no tenant scope")
    return Principal(
        subject=claims.get("sub", ""),
        tenant_id=tenant,
        scopes=tuple(claims.get("scope", "").split()),
    )


def _verify_jwt(token: str) -> dict:
    # Verify signature + iss/aud against JWKS. Kept thin here; jwt lib wired in prod.
    try:
        import jwt
        from jwt import PyJWKClient

        jwk_client = PyJWKClient(_settings.jwt_jwks_url)
        signing_key = jwk_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=_settings.jwt_audience,
            issuer=_settings.jwt_issuer,
        )
    except Exception as e:  # signature/expiry/aud failures all become 401
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {e}") from e


def enforce_tenant(principal: Principal, envelope_tenant: str) -> None:
    """Reject cross-tenant access: the envelope tenant must match the token tenant."""
    if principal.tenant_id != envelope_tenant:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "tenant mismatch between token and request")

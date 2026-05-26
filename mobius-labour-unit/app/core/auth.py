"""Bearer/JWT authentication and tenant authorization.

Every added endpoint declares HTTPBearer security; this dependency enforces it. In prod
it verifies the JWT against the configured JWKS and extracts tenant scope. For local/test
runs (auth_disabled=True) it accepts a dev token and a tenant header.

The raw bearer token from the inbound request is also stashed on a wrapper_api ContextVar
so every downstream wrapper call (ingest / retrieve / update) can send the same token to
the data wrapper service. When local auth is disabled and no inbound token exists, the
wrapper client can use its own configured fallback token.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.services import wrapper_api

_settings = get_settings()
_bearer = HTTPBearer(auto_error=not _settings.auth_disabled)


@dataclass
class Principal:
    subject: str
    tenant_id: str
    scopes: tuple[str, ...] = ()
    token: str | None = None


async def current_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    x_tenant_id: str | None = Header(default=None),
) -> Principal:
    # Forward the inbound bearer token verbatim to the wrapper service for every
    # subsequent ingest/retrieve/update made in this request.
    raw_token = creds.credentials if (creds and creds.credentials) else None
    wrapper_api.set_token(raw_token)

    if _settings.auth_disabled:
        # dev/test path: trust the tenant header, synthesize a principal
        return Principal(subject="dev", tenant_id=x_tenant_id or "dev-tenant", token=raw_token)

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
        token=raw_token,
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
    """Reject cross-tenant access: the envelope tenant must match the token tenant.

    Skipped entirely when GATEWAY_AUTH_DISABLED=true (local/test runs) — without a
    verified JWT there is no authoritative tenant to compare against.
    """
    if _settings.auth_disabled:
        return
    if principal.tenant_id != envelope_tenant:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "tenant mismatch between token and request")

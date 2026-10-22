"""Google OpenID Connect authorization-code flow helpers."""

from __future__ import annotations

import secrets
from hashlib import sha256
from urllib.parse import urlencode

import jwt

from tools.auth_config import AuthConfig


GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


class GoogleOAuthError(Exception):
    pass


def build_google_authorization_url(config: AuthConfig, *, state: str, nonce: str, code_challenge: str, intent: str) -> str:
    if not config.google_client_id or not config.google_client_secret or not config.google_redirect_uri:
        raise GoogleOAuthError("Google sign-in is not configured on this server.")
    params = {
        "client_id": config.google_client_id,
        "redirect_uri": config.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
        "include_granted_scopes": "true",
    }
    if intent == "signup":
        params["prompt"] = "select_account"
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def create_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = __import__("base64").urlsafe_b64encode(sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    return verifier, challenge


def exchange_google_code(config: AuthConfig, *, code: str, code_verifier: str) -> str:
    """Exchange an authorization code and return its ID token."""
    if not config.google_client_id or not config.google_client_secret or not config.google_redirect_uri:
        raise GoogleOAuthError("Google sign-in is not configured on this server.")
    import json
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    payload = urlencode(
        {
            "code": code,
            "client_id": config.google_client_id,
            "client_secret": config.google_client_secret,
            "redirect_uri": config.google_redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
    ).encode("ascii")
    request = Request(
        "https://oauth2.googleapis.com/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            token_result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            error_result = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            error_result = {}
        error_code = error_result.get("error") if isinstance(error_result, dict) else None
        error_description = error_result.get("error_description") if isinstance(error_result, dict) else None
        if isinstance(error_code, str):
            detail = error_code
            if isinstance(error_description, str) and error_description:
                detail = f"{detail}: {error_description[:240]}"
            raise GoogleOAuthError(f"Google token exchange rejected the request ({detail}).") from exc
        raise GoogleOAuthError(f"Google token exchange failed (HTTP {exc.code}).") from exc
    except (URLError, TimeoutError, ValueError) as exc:
        raise GoogleOAuthError("Google could not complete sign-in. Please try again.") from exc
    id_token = token_result.get("id_token") if isinstance(token_result, dict) else None
    if not id_token:
        raise GoogleOAuthError("Google did not return a verified identity token.")
    return str(id_token)


def verify_google_id_token(config: AuthConfig, *, id_token: str, expected_nonce: str) -> dict[str, object]:
    if not config.google_client_id:
        raise GoogleOAuthError("Google sign-in is not configured on this server.")
    try:
        signing_key = jwt.PyJWKClient(GOOGLE_JWKS_URL, timeout=10).get_signing_key_from_jwt(id_token).key
        claims = jwt.decode(
            id_token,
            signing_key,
            algorithms=["RS256"],
            audience=config.google_client_id,
            issuer=list(GOOGLE_ISSUERS),
            leeway=60,
            options={"require": ["exp", "iat", "sub", "email", "email_verified", "nonce"]},
        )
    except (jwt.PyJWTError, ValueError, TimeoutError) as exc:
        raise GoogleOAuthError("Google identity verification failed. Please try again.") from exc
    if claims.get("nonce") != expected_nonce:
        raise GoogleOAuthError("Google sign-in could not be verified. Please try again.")
    if claims.get("email_verified") is not True:
        raise GoogleOAuthError("Use a Google account with a verified email address.")
    return claims

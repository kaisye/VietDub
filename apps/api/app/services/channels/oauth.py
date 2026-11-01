"""OAuth orchestration: state + PKCE, callback token exchange, refresh, revoke.

Flow (installed-app pattern, RFC 8252):
    1. UI calls POST /api/channels/oauth/{platform}/start -> authorize_url + state.
    2. UI opens authorize_url in the system browser and polls /oauth/status/{state}.
    3. The platform redirects the browser to the loopback callback on this backend
       (http://127.0.0.1:<port>/api/channels/oauth/{platform}/callback), which
       validates ``state``, exchanges the code (+ PKCE verifier) and stores tokens.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from ...models import Channel, ChannelOAuthState
from . import PLATFORMS
from .credentials import get_credentials, get_redirect_base_url
from .crypto import SecretDecryptionError, decrypt_secret, encrypt_secret
from .providers import get_provider
from .providers.base import ChannelAuthError, ChannelProvider, ChannelProviderError, utcnow

logger = logging.getLogger(__name__)

STATE_TTL = timedelta(minutes=15)
REFRESH_MARGIN = timedelta(minutes=5)
CALLBACK_PATH = "/api/channels/oauth/{platform}/callback"


class ChannelConfigError(ValueError):
    """Platform credentials missing / invalid platform."""


class OAuthStateError(ValueError):
    """Unknown, expired, reused or mismatched ``state``."""


def new_code_verifier() -> str:
    # 64 random bytes -> 86 urlsafe chars (A-Z a-z 0-9 - _), within RFC 7636's 43..128.
    return secrets.token_urlsafe(64)


def new_state() -> str:
    return secrets.token_urlsafe(32)


# Facebook only exempts ``http://localhost`` (not the IP literal) from its HTTPS rule,
# and only while the Meta app is in Development mode.
LOOPBACK_HOST = {"youtube": "127.0.0.1", "tiktok": "127.0.0.1", "facebook": "localhost"}


def default_redirect_base(request_base_url: str | None = None, platform: str | None = None) -> str:
    """Loopback base the browser can reach. 127.0.0.1 for Google/TikTok (both recommend
    the IP literal over ``localhost``), ``localhost`` for Facebook; port taken from the
    incoming request or ``AETHER_API_PORT`` (desktop sidecar: 8386 dev / 18386 packaged)."""
    port = None
    if request_base_url:
        port = urlsplit(request_base_url).port
    if not port:
        port = int(os.getenv("AETHER_API_PORT", "8386"))
    host = LOOPBACK_HOST.get(platform or "", "127.0.0.1")
    return f"http://{host}:{port}"


def redirect_uri_for(platform: str, request_base_url: str | None = None) -> str:
    base = get_redirect_base_url() or default_redirect_base(request_base_url, platform)
    return base.rstrip("/") + CALLBACK_PATH.format(platform=platform)


def _require_platform(platform: str) -> None:
    if platform not in PLATFORMS:
        raise ChannelConfigError(f"Unknown platform '{platform}'.")


def start_authorization(db: Session, platform: str, request_base_url: str | None = None) -> dict:
    _require_platform(platform)
    creds = get_credentials(platform)
    if not creds.configured:
        raise ChannelConfigError(
            f"{platform}: client id and client secret are not configured (Settings > Channels)."
        )
    _purge_expired_states(db)
    provider = get_provider(platform)
    state = new_state()
    verifier = new_code_verifier()
    redirect_uri = redirect_uri_for(platform, request_base_url)
    now = utcnow()
    row = ChannelOAuthState(
        state=state,
        platform=platform,
        code_verifier_encrypted=encrypt_secret(verifier),
        redirect_uri=redirect_uri,
        status="pending",
        created_at=now,
        expires_at=now + STATE_TTL,
    )
    db.add(row)
    db.commit()
    return {
        "platform": platform,
        "state": state,
        "authorize_url": provider.build_authorize_url(creds, redirect_uri, state, verifier),
        "redirect_uri": redirect_uri,
        "expires_at": row.expires_at,
    }


def _purge_expired_states(db: Session) -> None:
    cutoff = utcnow() - timedelta(days=1)
    db.query(ChannelOAuthState).filter(ChannelOAuthState.expires_at < cutoff).delete()
    db.commit()


def get_state(db: Session, state: str) -> ChannelOAuthState | None:
    return db.get(ChannelOAuthState, state)


def state_view(row: ChannelOAuthState) -> dict:
    status = row.status
    if status == "pending" and row.expires_at < utcnow():
        status = "expired"
    return {
        "state": row.state,
        "platform": row.platform,
        "status": status,
        "error": row.error or None,
        "channel_ids": json.loads(row.channel_ids_json or "[]"),
        "created_at": row.created_at,
        "expires_at": row.expires_at,
        "completed_at": row.completed_at,
    }


def complete_authorization(
    db: Session,
    platform: str,
    *,
    state: str | None,
    code: str | None,
    error: str | None = None,
    error_description: str | None = None,
    provider: ChannelProvider | None = None,
) -> ChannelOAuthState:
    """Validate the callback and store the resulting channel(s).

    Raises OAuthStateError for a bad/expired/reused ``state`` (nothing is stored).
    Platform-side errors are recorded on the state row (status="error") and returned.
    """
    if not state:
        raise OAuthStateError("Missing state parameter.")
    row = db.get(ChannelOAuthState, state)
    if row is None or row.platform != platform:
        raise OAuthStateError("Unknown or mismatched state. Start the connection again from VietDub.")
    if row.status != "pending":
        raise OAuthStateError("This authorization was already used. Start the connection again.")
    now = utcnow()
    if row.expires_at < now:
        _fail(db, row, "Authorization expired. Start the connection again.")
        raise OAuthStateError("Authorization expired. Start the connection again.")
    if error:
        message = "Access was denied." if error in {"access_denied", "user_denied"} else f"{platform}: {error}"
        if error_description and error not in {"access_denied", "user_denied"}:
            message += f" ({error_description[:200]})"
        return _fail(db, row, message)
    if not code:
        return _fail(db, row, "Missing authorization code.")

    creds = get_credentials(platform)
    own_provider = provider is None
    provider = provider or get_provider(platform)
    try:
        verifier = decrypt_secret(row.code_verifier_encrypted)
        tokens = provider.exchange_code(creds, code, row.redirect_uri, verifier)
        accounts = provider.list_accounts(creds, tokens)
        channel_ids = [
            _upsert_connected_channel(db, platform, account).id for account in accounts
        ]
    except (ChannelProviderError, SecretDecryptionError) as exc:
        db.rollback()
        return _fail(db, row, str(exc))
    except Exception:  # never leak details / crash the callback page
        db.rollback()
        logger.exception("Channel OAuth callback failed for %s", platform)
        return _fail(db, row, f"{platform}: unexpected error while connecting.")
    finally:
        if own_provider:
            provider.close()
    row.status = "connected"
    row.error = ""
    row.channel_ids_json = json.dumps(channel_ids)
    row.completed_at = utcnow()
    row.code_verifier_encrypted = ""  # single use
    db.commit()
    return row


def _fail(db: Session, row: ChannelOAuthState, message: str) -> ChannelOAuthState:
    row.status = "error"
    row.error = message
    row.completed_at = utcnow()
    row.code_verifier_encrypted = ""
    db.commit()
    return row


def _upsert_connected_channel(db: Session, platform: str, account) -> Channel:
    profile = account.profile
    tokens = account.tokens
    now = utcnow()
    channel = (
        db.query(Channel)
        .filter(Channel.platform == platform, Channel.external_id == profile.external_id)
        .one_or_none()
    )
    if channel is None:
        channel = Channel(platform=platform, external_id=profile.external_id, created_at=now)
        db.add(channel)
    channel.source = "oauth"
    channel.account_external_id = account.account_external_id or tokens.account_id or ""
    channel.name = profile.name or channel.name
    channel.handle = profile.handle or channel.handle
    channel.url = profile.url or channel.url
    channel.avatar_url = profile.avatar_url or channel.avatar_url
    channel.status = "connected"
    channel.last_error = ""
    store_tokens(channel, tokens, now=now)
    channel.connected_at = now
    channel.updated_at = now
    db.flush()
    return channel


def store_tokens(channel: Channel, tokens, now=None) -> None:
    now = now or utcnow()
    channel.access_token_encrypted = encrypt_secret(tokens.access_token)
    if tokens.refresh_token:
        channel.refresh_token_encrypted = encrypt_secret(tokens.refresh_token)
    channel.token_expires_at = tokens.expires_at(now)
    if tokens.refresh_expires_in:
        channel.refresh_expires_at = tokens.refresh_expires_at(now)
    if tokens.scopes:
        channel.scopes = " ".join(tokens.scopes)


def ensure_access_token(db: Session, channel: Channel, provider: ChannelProvider) -> str:
    """Return a usable access token, refreshing (and persisting) it when near expiry.

    Raises ChannelAuthError when the user has to reconnect (status set to needs_reauth).
    """
    if channel.source != "oauth" or not channel.access_token_encrypted:
        raise ChannelAuthError("Channel is not connected via OAuth.")
    try:
        access = decrypt_secret(channel.access_token_encrypted)
        refresh = decrypt_secret(channel.refresh_token_encrypted)
    except SecretDecryptionError as exc:
        _mark_reauth(db, channel, str(exc))
        raise ChannelAuthError(str(exc)) from None
    expires = channel.token_expires_at
    if expires is None or expires - REFRESH_MARGIN > utcnow():
        return access
    return refresh_channel_token(db, channel, provider, refresh)


def refresh_channel_token(
    db: Session, channel: Channel, provider: ChannelProvider, refresh_token: str | None = None
) -> str:
    if refresh_token is None:
        refresh_token = decrypt_secret(channel.refresh_token_encrypted)
    creds = get_credentials(channel.platform)
    try:
        tokens = provider.refresh(creds, refresh_token, channel.external_id or "")
    except ChannelAuthError as exc:
        _mark_reauth(db, channel, str(exc))
        raise
    store_tokens(channel, tokens)
    channel.updated_at = utcnow()
    db.commit()
    return tokens.access_token


def _mark_reauth(db: Session, channel: Channel, message: str) -> None:
    channel.status = "needs_reauth"
    channel.last_error = message
    channel.updated_at = utcnow()
    db.commit()


def disconnect_channel(
    db: Session, channel: Channel, *, revoke: bool = True, provider: ChannelProvider | None = None
) -> dict:
    """Revoke (best effort) and wipe tokens. Keeps history; status -> disconnected."""
    revoked = False
    revoke_error = None
    if channel.source == "oauth" and revoke and channel.access_token_encrypted:
        own = provider is None
        provider = provider or get_provider(channel.platform)
        try:
            access = decrypt_secret(channel.access_token_encrypted)
            refresh = decrypt_secret(channel.refresh_token_encrypted)
            shared = _shares_login(db, channel)
            if channel.platform == "facebook" and shared:
                # Other Pages from the same Facebook login stay connected: revoking the
                # user grant would kill them too, so only forget this Page's token.
                revoked = False
            else:
                provider.revoke(get_credentials(channel.platform), access, refresh)
                revoked = True
        except (ChannelProviderError, SecretDecryptionError) as exc:
            revoke_error = str(exc)
        finally:
            if own:
                provider.close()
    channel.access_token_encrypted = ""
    channel.refresh_token_encrypted = ""
    channel.token_expires_at = None
    channel.refresh_expires_at = None
    if channel.source == "oauth":
        channel.status = "disconnected"
    channel.updated_at = utcnow()
    db.commit()
    return {"revoked": revoked, "revoke_error": revoke_error}


def _shares_login(db: Session, channel: Channel) -> bool:
    if not channel.account_external_id:
        return False
    return (
        db.query(Channel)
        .filter(
            Channel.platform == channel.platform,
            Channel.account_external_id == channel.account_external_id,
            Channel.id != channel.id,
            Channel.source == "oauth",
            Channel.access_token_encrypted != "",
        )
        .count()
        > 0
    )

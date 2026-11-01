"""Common provider interface + HTTP helpers shared by YouTube / Facebook / TikTok."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlencode

import httpx

from ..credentials import PlatformCredentials

DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)


def _default_client_factory() -> httpx.Client:
    return httpx.Client(timeout=DEFAULT_TIMEOUT, headers={"User-Agent": "VietDub-Studio/1.0"})


# Tests replace this with a factory returning ``httpx.Client(transport=httpx.MockTransport(...))``.
client_factory: Callable[[], httpx.Client] = _default_client_factory


class ChannelProviderError(RuntimeError):
    """Platform API call failed. Messages never include tokens or secrets."""


class ChannelAuthError(ChannelProviderError):
    """Token invalid / revoked / expired beyond refresh: user must reconnect."""


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str = ""
    expires_in: int | None = None  # seconds
    refresh_expires_in: int | None = None
    scopes: list[str] = field(default_factory=list)
    account_id: str = ""  # TikTok open_id / Facebook user id / Google subject (if known)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def expires_at(self, now: datetime | None = None) -> datetime | None:
        if not self.expires_in:
            return None
        return (now or utcnow()) + timedelta(seconds=int(self.expires_in))

    def refresh_expires_at(self, now: datetime | None = None) -> datetime | None:
        if not self.refresh_expires_in:
            return None
        return (now or utcnow()) + timedelta(seconds=int(self.refresh_expires_in))


@dataclass
class Profile:
    external_id: str
    name: str
    handle: str = ""
    url: str = ""
    avatar_url: str = ""


@dataclass
class Stats:
    followers: int | None = None
    views: int | None = None
    video_count: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None


@dataclass
class VideoItem:
    external_id: str
    title: str
    published_at: datetime | None = None
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    duration_seconds: int | None = None
    thumbnail_url: str = ""
    url: str = ""
    kind: str = "video"


@dataclass
class ConnectedAccount:
    """A channel discovered right after OAuth (Facebook can yield several Pages)."""

    profile: Profile
    tokens: TokenSet
    account_external_id: str = ""


def utcnow() -> datetime:
    """Naive UTC, matching the rest of the models (``datetime.utcnow`` defaults)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    # Facebook uses "+0000" (no colon) which fromisoformat accepts on 3.11+; normalise anyway.
    if len(text) > 5 and text[-5] in "+-" and text[-4:].isdigit() and ":" not in text[-5:]:
        text = text[:-2] + ":" + text[-2:]
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def pkce_challenge_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class ChannelProvider:
    platform: str = ""
    label: str = ""
    authorize_endpoint: str = ""
    default_scopes: tuple[str, ...] = ()
    scope_separator: str = " "
    docs_url: str = ""
    # True when one OAuth login can yield several channels (Facebook Pages).
    multi_account: bool = False

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    # -- HTTP -----------------------------------------------------------------
    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = client_factory()
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise ChannelProviderError(
                f"{self.label}: network error ({type(exc).__name__})."
            ) from None
        payload: Any
        try:
            payload = response.json() if response.content else {}
        except ValueError:
            payload = {"_text": response.text[:300]}
        if not isinstance(payload, dict):
            payload = {"data": payload}
        self.check_response(response.status_code, payload)
        return payload

    def check_response(self, status_code: int, payload: dict[str, Any]) -> None:
        """Raise on HTTP/API errors. Subclasses parse platform-specific bodies."""
        if status_code in (401,):
            raise ChannelAuthError(f"{self.label}: access token rejected ({status_code}).")
        if status_code >= 400:
            raise ChannelProviderError(f"{self.label}: API error HTTP {status_code}.")

    # -- OAuth ----------------------------------------------------------------
    def scopes(self, creds: PlatformCredentials) -> list[str]:
        if creds.scopes_override:
            return [s for s in creds.scopes_override.replace(",", " ").split() if s]
        return list(self.default_scopes)

    def pkce_challenge(self, verifier: str) -> str:
        return pkce_challenge_s256(verifier)

    def authorize_params(
        self, creds: PlatformCredentials, redirect_uri: str, state: str, code_challenge: str
    ) -> dict[str, str]:
        raise NotImplementedError

    def build_authorize_url(
        self, creds: PlatformCredentials, redirect_uri: str, state: str, code_verifier: str
    ) -> str:
        params = self.authorize_params(creds, redirect_uri, state, self.pkce_challenge(code_verifier))
        return f"{self.authorize_endpoint}?{urlencode(params)}"

    def exchange_code(
        self, creds: PlatformCredentials, code: str, redirect_uri: str, code_verifier: str
    ) -> TokenSet:
        raise NotImplementedError

    def refresh(self, creds: PlatformCredentials, refresh_token: str, external_id: str) -> TokenSet:
        raise NotImplementedError

    def revoke(self, creds: PlatformCredentials, access_token: str, refresh_token: str) -> None:
        raise NotImplementedError

    def list_accounts(self, creds: PlatformCredentials, tokens: TokenSet) -> list[ConnectedAccount]:
        """Channels reachable with a fresh token set (default: the single profile)."""
        profile = self.fetch_profile(tokens.access_token, tokens.account_id)
        return [ConnectedAccount(profile=profile, tokens=tokens, account_external_id=tokens.account_id)]

    # -- Data -----------------------------------------------------------------
    def fetch_profile(self, access_token: str, external_id: str) -> Profile:
        raise NotImplementedError

    def fetch_stats(self, access_token: str, external_id: str) -> Stats:
        raise NotImplementedError

    def fetch_recent_videos(self, access_token: str, external_id: str, limit: int = 50) -> list[VideoItem]:
        raise NotImplementedError

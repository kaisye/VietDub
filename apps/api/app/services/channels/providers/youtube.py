"""YouTube: Google OAuth 2.0 for installed apps (PKCE S256, loopback redirect) + Data API v3.

Docs: https://developers.google.com/identity/protocols/oauth2/native-app
      https://developers.google.com/youtube/v3/docs
"""

from __future__ import annotations

import re
from typing import Any

from .base import (
    ChannelAuthError,
    ChannelProvider,
    ChannelProviderError,
    ConnectedAccount,
    Profile,
    Stats,
    TokenSet,
    VideoItem,
    parse_iso_datetime,
    to_int,
)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/youtube/v3"

_DURATION_RE = re.compile(
    r"^P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+(?:\.\d+)?)S)?)?$"
)


def parse_iso_duration(value: str | None) -> int | None:
    if not value:
        return None
    match = _DURATION_RE.match(value.strip())
    if not match:
        return None
    parts = {key: float(val) if val else 0.0 for key, val in match.groupdict().items()}
    return int(parts["d"] * 86400 + parts["h"] * 3600 + parts["m"] * 60 + parts["s"])


def _best_thumbnail(thumbnails: dict[str, Any] | None) -> str:
    thumbnails = thumbnails or {}
    for key in ("maxres", "standard", "high", "medium", "default"):
        url = (thumbnails.get(key) or {}).get("url")
        if url:
            return str(url)
    return ""


class YouTubeProvider(ChannelProvider):
    platform = "youtube"
    label = "YouTube"
    authorize_endpoint = AUTH_URL
    # Read-only channel + video statistics. (yt-analytics.readonly is not requested:
    # Data API statistics cover the dashboard and it avoids an extra sensitive scope.)
    default_scopes = ("https://www.googleapis.com/auth/youtube.readonly",)
    docs_url = "https://developers.google.com/identity/protocols/oauth2/native-app"

    def check_response(self, status_code: int, payload: dict[str, Any]) -> None:
        if status_code < 400:
            return
        error = payload.get("error")
        if isinstance(error, str):  # OAuth endpoint errors: {"error": "invalid_grant", ...}
            if error in {"invalid_grant", "unauthorized_client", "invalid_client"}:
                raise ChannelAuthError(f"YouTube: authorization rejected ({error}).")
            raise ChannelProviderError(f"YouTube: OAuth error ({error}).")
        reason = ""
        message = ""
        if isinstance(error, dict):
            message = str(error.get("message") or "")[:200]
            errors = error.get("errors") or []
            if errors and isinstance(errors[0], dict):
                reason = str(errors[0].get("reason") or "")
        if status_code == 401:
            raise ChannelAuthError("YouTube: access token rejected (401).")
        if reason in {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}:
            raise ChannelProviderError("YouTube: API quota exceeded, try again later.")
        raise ChannelProviderError(f"YouTube: API error HTTP {status_code} {reason} {message}".strip())

    # -- OAuth -----------------------------------------------------------------
    def authorize_params(self, creds, redirect_uri, state, code_challenge):
        return {
            "client_id": creds.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.scopes(creds)),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            # Force the consent screen so Google re-issues a refresh token on reconnect.
            "prompt": "consent",
            "include_granted_scopes": "true",
        }

    def _token_set(self, payload: dict[str, Any], fallback_refresh: str = "") -> TokenSet:
        if not payload.get("access_token"):
            raise ChannelProviderError("YouTube: token response missing access_token.")
        return TokenSet(
            access_token=str(payload["access_token"]),
            # Google omits refresh_token on refresh responses; keep the old one.
            refresh_token=str(payload.get("refresh_token") or fallback_refresh),
            expires_in=to_int(payload.get("expires_in")),
            scopes=str(payload.get("scope") or "").split(),
            raw=payload,
        )

    def exchange_code(self, creds, code, redirect_uri, code_verifier):
        data = {
            "client_id": creds.client_id,
            "code": code,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
        if creds.client_secret:
            data["client_secret"] = creds.client_secret
        return self._token_set(self.request("POST", TOKEN_URL, data=data))

    def refresh(self, creds, refresh_token, external_id):
        if not refresh_token:
            raise ChannelAuthError("YouTube: no refresh token stored, reconnect the channel.")
        data = {
            "client_id": creds.client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        if creds.client_secret:
            data["client_secret"] = creds.client_secret
        return self._token_set(self.request("POST", TOKEN_URL, data=data), fallback_refresh=refresh_token)

    def revoke(self, creds, access_token, refresh_token):
        # Revoking the refresh token also invalidates its access tokens.
        token = refresh_token or access_token
        if token:
            self.request("POST", REVOKE_URL, data={"token": token})

    # -- Data ------------------------------------------------------------------
    def _headers(self, access_token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    def _channel(self, access_token: str, external_id: str) -> dict[str, Any]:
        params: dict[str, str] = {"part": "snippet,statistics,contentDetails"}
        if external_id:
            params["id"] = external_id
        else:
            params["mine"] = "true"
        payload = self.request("GET", f"{API}/channels", params=params, headers=self._headers(access_token))
        items = payload.get("items") or []
        if not items:
            raise ChannelProviderError("YouTube: this Google account has no YouTube channel.")
        return items[0]

    def list_accounts(self, creds, tokens):
        item = self._channel(tokens.access_token, "")
        profile = self._profile(item)
        return [_account(profile, tokens)]

    def _profile(self, item: dict[str, Any]) -> Profile:
        snippet = item.get("snippet") or {}
        handle = str(snippet.get("customUrl") or "")
        channel_id = str(item.get("id") or "")
        url = f"https://www.youtube.com/{handle}" if handle.startswith("@") else f"https://www.youtube.com/channel/{channel_id}"
        return Profile(
            external_id=channel_id,
            name=str(snippet.get("title") or ""),
            handle=handle,
            url=url,
            avatar_url=_best_thumbnail(snippet.get("thumbnails")),
        )

    def fetch_profile(self, access_token, external_id):
        return self._profile(self._channel(access_token, external_id))

    def fetch_stats(self, access_token, external_id):
        stats = self._channel(access_token, external_id).get("statistics") or {}
        return Stats(
            followers=None if stats.get("hiddenSubscriberCount") else to_int(stats.get("subscriberCount")),
            views=to_int(stats.get("viewCount")),
            video_count=to_int(stats.get("videoCount")),
        )

    def fetch_recent_videos(self, access_token, external_id, limit=50):
        channel = self._channel(access_token, external_id)
        uploads = ((channel.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        if not uploads:
            return []
        playlist = self.request(
            "GET",
            f"{API}/playlistItems",
            params={"part": "contentDetails", "playlistId": uploads, "maxResults": str(min(max(limit, 1), 50))},
            headers=self._headers(access_token),
        )
        ids = [
            str((entry.get("contentDetails") or {}).get("videoId"))
            for entry in playlist.get("items") or []
            if (entry.get("contentDetails") or {}).get("videoId")
        ]
        if not ids:
            return []
        videos = self.request(
            "GET",
            f"{API}/videos",
            params={"part": "snippet,statistics,contentDetails", "id": ",".join(ids), "maxResults": "50"},
            headers=self._headers(access_token),
        )
        return [self.map_video(item) for item in videos.get("items") or []]

    @staticmethod
    def map_video(item: dict[str, Any]) -> VideoItem:
        snippet = item.get("snippet") or {}
        stats = item.get("statistics") or {}
        video_id = str(item.get("id") or "")
        return VideoItem(
            external_id=video_id,
            title=str(snippet.get("title") or ""),
            published_at=parse_iso_datetime(snippet.get("publishedAt")),
            views=to_int(stats.get("viewCount")),
            likes=to_int(stats.get("likeCount")),
            comments=to_int(stats.get("commentCount")),
            duration_seconds=parse_iso_duration((item.get("contentDetails") or {}).get("duration")),
            thumbnail_url=_best_thumbnail(snippet.get("thumbnails")),
            url=f"https://www.youtube.com/watch?v={video_id}",
        )


def _account(profile: Profile, tokens: TokenSet) -> ConnectedAccount:
    tokens.account_id = tokens.account_id or profile.external_id
    return ConnectedAccount(profile=profile, tokens=tokens, account_external_id=profile.external_id)

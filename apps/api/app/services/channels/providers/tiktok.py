"""TikTok: Login Kit for Desktop (v2 OAuth, PKCE with *hex* SHA-256 challenge) + Display API.

Docs: https://developers.tiktok.com/doc/login-kit-desktop
      https://developers.tiktok.com/doc/oauth-user-access-token-management
      https://developers.tiktok.com/doc/tiktok-api-v2-get-user-info
      https://developers.tiktok.com/doc/tiktok-api-v2-video-list
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from .base import (
    ChannelAuthError,
    ChannelProvider,
    ChannelProviderError,
    Profile,
    Stats,
    TokenSet,
    VideoItem,
    to_int,
)

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
REVOKE_URL = "https://open.tiktokapis.com/v2/oauth/revoke/"
USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"
VIDEO_LIST_URL = "https://open.tiktokapis.com/v2/video/list/"

PROFILE_FIELDS = "open_id,union_id,avatar_url,display_name,username,profile_deep_link"
STATS_FIELDS = "open_id,follower_count,following_count,likes_count,video_count"
BASIC_FIELDS = "open_id,union_id,avatar_url,display_name"
VIDEO_FIELDS = (
    "id,title,video_description,create_time,cover_image_url,share_url,duration,"
    "view_count,like_count,comment_count,share_count"
)
_AUTH_ERRORS = {"access_token_invalid", "invalid_grant", "invalid_token", "token_expired", "invalid_client"}
_FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"}


class TikTokProvider(ChannelProvider):
    platform = "tiktok"
    label = "TikTok"
    authorize_endpoint = AUTH_URL
    scope_separator = ","
    default_scopes = ("user.info.basic", "user.info.profile", "user.info.stats", "video.list")
    docs_url = "https://developers.tiktok.com/doc/login-kit-desktop"

    def pkce_challenge(self, verifier: str) -> str:
        # TikTok Login Kit for Desktop documents the challenge as hex(SHA256(verifier)),
        # not RFC 7636 base64url. See docs/channels-oauth.md.
        return hashlib.sha256(verifier.encode("ascii")).hexdigest()

    def check_response(self, status_code: int, payload: dict[str, Any]) -> None:
        error = payload.get("error")
        if isinstance(error, str) and error:  # OAuth endpoints: {"error": "...", "error_description": ...}
            if error in _AUTH_ERRORS:
                raise ChannelAuthError(f"TikTok: authorization rejected ({error}).")
            raise ChannelProviderError(f"TikTok: OAuth error ({error}).")
        code = ""
        if isinstance(error, dict):
            code = str(error.get("code") or "")
        if code and code != "ok":
            if code in _AUTH_ERRORS:
                raise ChannelAuthError(f"TikTok: session expired or revoked ({code}). Reconnect the account.")
            if code == "rate_limit_exceeded":
                raise ChannelProviderError("TikTok: API rate limit reached, try again later.")
            raise ChannelProviderError(f"TikTok: API error ({code}).")
        if status_code == 401:
            raise ChannelAuthError("TikTok: access token rejected (401).")
        if status_code >= 400:
            raise ChannelProviderError(f"TikTok: API error HTTP {status_code}.")

    # -- OAuth -----------------------------------------------------------------
    def authorize_params(self, creds, redirect_uri, state, code_challenge):
        return {
            "client_key": creds.client_id,
            "response_type": "code",
            "scope": ",".join(self.scopes(creds)),
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

    def _token_set(self, payload: dict[str, Any]) -> TokenSet:
        if not payload.get("access_token"):
            raise ChannelProviderError("TikTok: token response missing access_token.")
        return TokenSet(
            access_token=str(payload["access_token"]),
            refresh_token=str(payload.get("refresh_token") or ""),
            expires_in=to_int(payload.get("expires_in")),
            refresh_expires_in=to_int(payload.get("refresh_expires_in")),
            scopes=[s for s in str(payload.get("scope") or "").split(",") if s],
            account_id=str(payload.get("open_id") or ""),
        )

    def exchange_code(self, creds, code, redirect_uri, code_verifier):
        data = {
            "client_key": creds.client_id,
            "client_secret": creds.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }
        return self._token_set(self.request("POST", TOKEN_URL, data=data, headers=_FORM_HEADERS))

    def refresh(self, creds, refresh_token, external_id):
        if not refresh_token:
            raise ChannelAuthError("TikTok: no refresh token stored, reconnect the account.")
        data = {
            "client_key": creds.client_id,
            "client_secret": creds.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        # TikTok may rotate the refresh token: the caller stores whatever comes back.
        return self._token_set(self.request("POST", TOKEN_URL, data=data, headers=_FORM_HEADERS))

    def revoke(self, creds, access_token, refresh_token):
        if access_token:
            self.request(
                "POST",
                REVOKE_URL,
                data={"client_key": creds.client_id, "client_secret": creds.client_secret, "token": access_token},
                headers=_FORM_HEADERS,
            )

    # -- Data ------------------------------------------------------------------
    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _user(self, access_token: str, fields: str) -> dict[str, Any]:
        payload = self.request("GET", USER_INFO_URL, params={"fields": fields}, headers=self._headers(access_token))
        return (payload.get("data") or {}).get("user") or {}

    def fetch_profile(self, access_token, external_id):
        try:
            user = self._user(access_token, PROFILE_FIELDS)
        except ChannelAuthError:
            raise
        except ChannelProviderError:
            # user.info.profile is optional; fall back to user.info.basic fields.
            user = self._user(access_token, BASIC_FIELDS)
        username = str(user.get("username") or "")
        open_id = str(user.get("open_id") or external_id)
        return Profile(
            external_id=open_id,
            name=str(user.get("display_name") or username or "TikTok"),
            handle=f"@{username}" if username else "",
            url=str(user.get("profile_deep_link") or (f"https://www.tiktok.com/@{username}" if username else "")),
            avatar_url=str(user.get("avatar_url") or ""),
        )

    def fetch_stats(self, access_token, external_id):
        user = self._user(access_token, STATS_FIELDS)
        return Stats(
            followers=to_int(user.get("follower_count")),
            video_count=to_int(user.get("video_count")),
            # likes_count is the lifetime total of likes received across all videos.
            likes=to_int(user.get("likes_count")),
        )

    def fetch_recent_videos(self, access_token, external_id, limit=50):
        items: list[VideoItem] = []
        cursor: int | None = None
        for _ in range(3):  # at most 60 videos per sync
            body: dict[str, Any] = {"max_count": 20}
            if cursor:
                body["cursor"] = cursor
            payload = self.request(
                "POST",
                VIDEO_LIST_URL,
                params={"fields": VIDEO_FIELDS},
                json=body,
                headers={**self._headers(access_token), "Content-Type": "application/json"},
            )
            data = payload.get("data") or {}
            items.extend(self.map_video(video) for video in data.get("videos") or [])
            if not data.get("has_more") or len(items) >= limit:
                break
            cursor = to_int(data.get("cursor"))
        return items[:limit]

    @staticmethod
    def map_video(video: dict[str, Any]) -> VideoItem:
        created = to_int(video.get("create_time"))
        return VideoItem(
            external_id=str(video.get("id") or ""),
            title=str(video.get("title") or video.get("video_description") or "")[:1000],
            published_at=(
                datetime.fromtimestamp(created, tz=timezone.utc).replace(tzinfo=None) if created else None
            ),
            views=to_int(video.get("view_count")),
            likes=to_int(video.get("like_count")),
            comments=to_int(video.get("comment_count")),
            shares=to_int(video.get("share_count")),
            duration_seconds=to_int(video.get("duration")),
            thumbnail_url=str(video.get("cover_image_url") or ""),
            url=str(video.get("share_url") or ""),
        )

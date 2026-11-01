"""Facebook Pages: Facebook Login (manual code flow, state + PKCE) + Graph API.

One login can manage several Pages: each Page becomes its own Channel holding a
Page access token. Page tokens minted from a *long-lived* user token have no
expiry; we keep the long-lived user token (≈60 days) encrypted in
``refresh_token_encrypted`` so a revoked/rotated Page token can be re-minted.

Docs: https://developers.facebook.com/docs/facebook-login/guides/advanced/manual-flow
      https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived
      https://developers.facebook.com/docs/graph-api/reference/page/
"""

from __future__ import annotations

import os
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

DEFAULT_GRAPH_VERSION = "v26.0"
_AUTH_CODES = {102, 190, 463, 467}
_RATE_CODES = {4, 17, 32, 613}
_PERMISSION_CODES = {10, 200, 299}


def graph_version() -> str:
    return os.getenv("AETHER_FACEBOOK_GRAPH_VERSION", "").strip() or DEFAULT_GRAPH_VERSION


def pkce_enabled() -> bool:
    return os.getenv("AETHER_FACEBOOK_PKCE", "1").strip().lower() not in {"0", "false", "no", "off"}


class FacebookProvider(ChannelProvider):
    platform = "facebook"
    label = "Facebook"
    multi_account = True
    scope_separator = ","
    default_scopes = ("pages_show_list", "pages_read_engagement", "read_insights")
    docs_url = "https://developers.facebook.com/docs/facebook-login/guides/advanced/manual-flow"

    @property
    def authorize_endpoint(self) -> str:  # type: ignore[override]
        return f"https://www.facebook.com/{graph_version()}/dialog/oauth"

    @property
    def graph(self) -> str:
        return f"https://graph.facebook.com/{graph_version()}"

    def check_response(self, status_code: int, payload: dict[str, Any]) -> None:
        error = payload.get("error")
        if status_code < 400 and not error:
            return
        code = None
        message = ""
        if isinstance(error, dict):
            code = to_int(error.get("code"))
            message = str(error.get("message") or "")[:200]
            # Graph messages may echo the request but never include the token value.
        if code in _AUTH_CODES or status_code == 401:
            raise ChannelAuthError(f"Facebook: session expired or revoked ({code}). Reconnect the Page.")
        if code in _RATE_CODES:
            raise ChannelProviderError("Facebook: API rate limit reached, try again later.")
        if code in _PERMISSION_CODES:
            raise ChannelProviderError(f"Facebook: missing permission ({code}). {message}".strip())
        raise ChannelProviderError(f"Facebook: API error HTTP {status_code} ({code}) {message}".strip())

    # -- OAuth -----------------------------------------------------------------
    def authorize_params(self, creds, redirect_uri, state, code_challenge):
        params = {
            "client_id": creds.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "response_type": "code",
            "scope": ",".join(self.scopes(creds)),
        }
        if pkce_enabled():
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        return params

    def exchange_code(self, creds, code, redirect_uri, code_verifier):
        params = {
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        }
        if pkce_enabled():
            params["code_verifier"] = code_verifier
        short = self.request("GET", f"{self.graph}/oauth/access_token", params=params)
        if not short.get("access_token"):
            raise ChannelProviderError("Facebook: token response missing access_token.")
        # Upgrade to a long-lived (~60 day) user token; Page tokens derived from it never expire.
        long_lived = self.request(
            "GET",
            f"{self.graph}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "fb_exchange_token": short["access_token"],
            },
        )
        user_token = str(long_lived.get("access_token") or short["access_token"])
        me = self.request("GET", f"{self.graph}/me", params={"fields": "id,name"}, headers=self._headers(user_token))
        return TokenSet(
            access_token=user_token,
            expires_in=to_int(long_lived.get("expires_in") or short.get("expires_in")),
            account_id=str(me.get("id") or ""),
            raw={"token_type": long_lived.get("token_type")},
        )

    def list_accounts(self, creds, tokens):
        user_token = tokens.access_token
        accounts: list[ConnectedAccount] = []
        url: str | None = f"{self.graph}/me/accounts"
        params: dict[str, str] | None = {
            "fields": "id,name,username,link,picture{url},access_token,followers_count,fan_count",
            "limit": "100",
        }
        pages_seen = 0
        while url and pages_seen < 10:
            payload = self.request("GET", url, params=params, headers=self._headers(user_token))
            for page in payload.get("data") or []:
                if not page.get("id") or not page.get("access_token"):
                    continue
                accounts.append(
                    ConnectedAccount(
                        profile=self._profile(page),
                        tokens=TokenSet(
                            access_token=str(page["access_token"]),
                            refresh_token=user_token,
                            expires_in=None,
                            refresh_expires_in=tokens.expires_in,
                            scopes=self.scopes(creds),
                            account_id=tokens.account_id,
                        ),
                        account_external_id=tokens.account_id,
                    )
                )
            url = (payload.get("paging") or {}).get("next")
            params = None  # "next" already carries the query string
            pages_seen += 1
        if not accounts:
            raise ChannelProviderError(
                "Facebook: no Pages found. Make sure you selected at least one Page in the login dialog "
                "and have a role on it (Business-portfolio Pages may also need business_management)."
            )
        return accounts

    def refresh(self, creds, refresh_token, external_id):
        """Re-mint the Page token from the stored long-lived user token."""
        if not refresh_token:
            raise ChannelAuthError("Facebook: no user token stored, reconnect the Page.")
        payload = self.request(
            "GET",
            f"{self.graph}/{external_id}",
            params={"fields": "access_token"},
            headers=self._headers(refresh_token),
        )
        if not payload.get("access_token"):
            raise ChannelAuthError("Facebook: you no longer manage this Page. Reconnect it.")
        return TokenSet(access_token=str(payload["access_token"]), refresh_token=refresh_token)

    def revoke(self, creds, access_token, refresh_token):
        # DELETE /me/permissions with the *user* token removes the app's grant for the
        # whole Facebook login (all Pages). The caller only does this for the last Page.
        token = refresh_token or access_token
        if token:
            self.request("DELETE", f"{self.graph}/me/permissions", headers=self._headers(token))

    # -- Data ------------------------------------------------------------------
    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    @staticmethod
    def _profile(page: dict[str, Any]) -> Profile:
        picture = ((page.get("picture") or {}).get("data") or {}).get("url") or ""
        page_id = str(page.get("id") or "")
        username = str(page.get("username") or "")
        return Profile(
            external_id=page_id,
            name=str(page.get("name") or ""),
            handle=f"@{username}" if username else "",
            url=str(page.get("link") or f"https://www.facebook.com/{page_id}"),
            avatar_url=str(picture),
        )

    def _page(self, access_token: str, page_id: str) -> dict[str, Any]:
        return self.request(
            "GET",
            f"{self.graph}/{page_id}",
            params={"fields": "id,name,username,link,picture{url},followers_count,fan_count"},
            headers=self._headers(access_token),
        )

    def fetch_profile(self, access_token, external_id):
        return self._profile(self._page(access_token, external_id))

    def fetch_stats(self, access_token, external_id):
        page = self._page(access_token, external_id)
        followers = to_int(page.get("followers_count"))
        if followers is None:
            followers = to_int(page.get("fan_count"))
        return Stats(followers=followers)

    def fetch_recent_videos(self, access_token, external_id, limit=25):
        headers = self._headers(access_token)
        limit = max(1, min(limit, 25))
        items: list[VideoItem] = []
        videos = self.request(
            "GET",
            f"{self.graph}/{external_id}/videos",
            params={
                "fields": "id,title,description,created_time,length,permalink_url,picture,"
                "likes.summary(true).limit(0),comments.summary(true).limit(0)",
                "limit": str(limit),
            },
            headers=headers,
        )
        for video in videos.get("data") or []:
            item = self.map_video(video)
            item.views = self._video_views(access_token, item.external_id)
            items.append(item)
        try:
            posts = self.request(
                "GET",
                f"{self.graph}/{external_id}/posts",
                params={
                    "fields": "id,message,created_time,permalink_url,full_picture,shares,"
                    "reactions.summary(total_count).limit(0),comments.summary(true).limit(0)",
                    "limit": str(limit),
                },
                headers=headers,
            )
            items.extend(self.map_post(post) for post in posts.get("data") or [])
        except ChannelAuthError:
            raise
        except ChannelProviderError:
            pass  # posts need pages_read_engagement; videos alone are still useful
        return items

    def _video_views(self, access_token: str, video_id: str) -> int | None:
        """Lifetime views from Video Insights (needs read_insights); None when not permitted."""
        try:
            payload = self.request(
                "GET",
                f"{self.graph}/{video_id}/video_insights",
                params={"metric": "total_video_views", "period": "lifetime"},
                headers=self._headers(access_token),
            )
        except ChannelAuthError:
            raise
        except ChannelProviderError:
            return None
        for metric in payload.get("data") or []:
            values = metric.get("values") or []
            if values:
                return to_int(values[0].get("value"))
        return None

    @staticmethod
    def _summary_count(node: Any) -> int | None:
        if not isinstance(node, dict):
            return None
        return to_int((node.get("summary") or {}).get("total_count"))

    @classmethod
    def map_video(cls, video: dict[str, Any]) -> VideoItem:
        permalink = str(video.get("permalink_url") or "")
        if permalink.startswith("/"):
            permalink = f"https://www.facebook.com{permalink}"
        length = video.get("length")
        return VideoItem(
            external_id=str(video.get("id") or ""),
            title=str(video.get("title") or video.get("description") or "")[:1000],
            published_at=parse_iso_datetime(video.get("created_time")),
            likes=cls._summary_count(video.get("likes")),
            comments=cls._summary_count(video.get("comments")),
            duration_seconds=int(float(length)) if length not in (None, "") else None,
            thumbnail_url=str(video.get("picture") or ""),
            url=permalink,
            kind="video",
        )

    @classmethod
    def map_post(cls, post: dict[str, Any]) -> VideoItem:
        return VideoItem(
            external_id=str(post.get("id") or ""),
            title=str(post.get("message") or "")[:1000],
            published_at=parse_iso_datetime(post.get("created_time")),
            likes=cls._summary_count(post.get("reactions")),
            comments=cls._summary_count(post.get("comments")),
            shares=to_int((post.get("shares") or {}).get("count")),
            thumbnail_url=str(post.get("full_picture") or ""),
            url=str(post.get("permalink_url") or ""),
            kind="post",
        )

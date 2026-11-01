# Connecting channels (YouTube, Facebook Pages, TikTok)

VietDub Studio reads channel statistics and recent videos from YouTube, Facebook
Pages and TikTok to power the multi-channel dashboard. Because VietDub is an
open-source desktop app, **there is no shared VietDub OAuth app**: every user
creates their own developer app on each platform and pastes its client id /
secret into **Settings → Channels**. Nothing leaves your machine except the
calls to the platforms themselves.

You do not need OAuth to use the dashboard: a channel can be added manually
(platform + name + URL) with metrics typed in or imported from CSV.

---

## How the connection works

VietDub uses the OAuth 2.0 *installed application* pattern (RFC 8252):

1. The app asks the local backend for an authorization URL
   (`POST /api/channels/oauth/{platform}/start`). The backend creates a random
   `state` (CSRF protection, single use, valid 15 minutes) and a PKCE
   `code_verifier` (stored encrypted).
2. The URL opens in your **system browser** (not an embedded webview), where you
   log in and approve read-only access.
3. The platform redirects the browser to the backend's **loopback callback**:

   | Platform | Default redirect URI |
   |---|---|
   | YouTube  | `http://127.0.0.1:<port>/api/channels/oauth/youtube/callback` |
   | Facebook | `http://localhost:<port>/api/channels/oauth/facebook/callback` |
   | TikTok   | `http://127.0.0.1:<port>/api/channels/oauth/tiktok/callback` |

   `<port>` is the backend port: **8386** when running from source / dev build,
   **18386** in the packaged desktop app (or whatever `AETHER_API_PORT` says).
   The exact URIs are shown in Settings → Channels — copy them from there.
4. The backend validates `state`, exchanges the code (with the PKCE verifier),
   stores the tokens encrypted, shows "Đã kết nối, bạn có thể đóng tab này" and
   starts a first sync. The app notices by polling
   `GET /api/channels/oauth/status/{state}`.

### Custom redirect base URL (workaround)

If a platform refuses a loopback/`http` redirect, set **Redirect base URL** in
Settings → Channels (or `AETHER_CHANNELS_REDIRECT_BASE_URL`) to an HTTPS URL that
forwards to the local backend, e.g. an ngrok/Cloudflare tunnel:

```
ngrok http 8386 --domain=my-vietdub.ngrok-free.app
# Redirect base URL: https://my-vietdub.ngrok-free.app
# -> https://my-vietdub.ngrok-free.app/api/channels/oauth/facebook/callback
```

The base URL applies to all three platforms; register the resulting URIs on each
platform. Only the callback needs to be reachable — `state` + PKCE protect it.

### Where secrets live

* Client secrets: `<storage>/channel-credentials.json`, Fernet-encrypted
  (client ids are stored in clear: they are public by design).
* Access / refresh tokens: SQLite table `channels`, Fernet-encrypted.
* Encryption key: `<storage>/secrets/channels.key` (created with mode 0600) or
  `AETHER_CHANNELS_SECRET_KEY`. Deleting the key makes stored tokens unreadable:
  channels switch to "needs reconnect" and secrets must be re-entered.
* The API never returns secrets or tokens (secrets are shown as `••••••abcd`).

Environment fallbacks (used when nothing is saved in Settings):
`AETHER_YOUTUBE_CLIENT_ID`, `AETHER_YOUTUBE_CLIENT_SECRET`,
`AETHER_FACEBOOK_CLIENT_ID`, `AETHER_FACEBOOK_CLIENT_SECRET`,
`AETHER_TIKTOK_CLIENT_ID` (or `AETHER_TIKTOK_CLIENT_KEY`), `AETHER_TIKTOK_CLIENT_SECRET`.

---

## YouTube (Google Cloud)

1. <https://console.cloud.google.com/> → create a project.
2. **APIs & Services → Library** → enable **YouTube Data API v3**.
3. **Google Auth Platform → Branding / Audience**: user type *External*; add your
   Google account(s) as **test users** while the app is in *Testing*.
4. **Data Access → Add scope**: `https://www.googleapis.com/auth/youtube.readonly`.
5. **Clients → Create client → Application type: Desktop app**. Copy the client
   id and client secret into VietDub.
6. Nothing else to register: Desktop clients accept any loopback port on
   `http://127.0.0.1:<port>` automatically.

Flow details: authorization endpoint `https://accounts.google.com/o/oauth2/v2/auth`
with PKCE `S256`, `access_type=offline`, `prompt=consent` (so a refresh token is
re-issued on reconnect); token endpoint `https://oauth2.googleapis.com/token`;
revoke `https://oauth2.googleapis.com/revoke` (revoking the refresh token also
kills access tokens). Access tokens last ~1 h and are refreshed automatically.

Data: `channels.list(mine=true, part=snippet,statistics,contentDetails)`, the
uploads playlist via `playlistItems.list` (latest 50) and `videos.list` for
statistics. Quota cost per sync ≈ 5 units (default quota 10,000/day).

Review / limits:
* `youtube.readonly` is a **sensitive** scope. While the app is in *Testing*
  only listed test users (max 100) can connect and **refresh tokens expire after
  7 days** — you will need to reconnect weekly. For personal use that is
  usually acceptable; otherwise submit the app for Google verification
  (privacy policy + demo video) and switch to *In production*.
* Hidden subscriber counts are reported as unknown (`null`).
* YouTube Analytics API (watch time, demographics) is not used yet, to avoid an
  extra scope.

## Facebook Pages (Meta for Developers)

1. <https://developers.facebook.com/apps> → **Create app** → use case
   *"Manage everything on your Page"* (or type *Business*), which adds
   **Facebook Login for Business / Facebook Login**.
2. **App settings → Basic**: copy **App ID** (client id) and **App Secret**
   (client secret) into VietDub.
3. **Facebook Login → Settings**:
   * *Client OAuth login*: **On**, *Web OAuth login*: **On**.
   * *Valid OAuth Redirect URIs*: add the Facebook URI shown in VietDub
     (`http://localhost:<port>/api/channels/oauth/facebook/callback`).
     Meta lets `http://localhost` redirects through **only while the app is in
     Development mode** (and only for people with a role on the app). With
     *Enforce HTTPS* on (default) a Live app needs an **https** redirect —
     use the redirect-base-URL tunnel workaround above.
4. Permissions requested: `pages_show_list`, `pages_read_engagement`,
   `read_insights`. In Development mode they work for app admins/developers/
   testers without review. To let other people connect, the app needs
   **Business verification** + **App Review** (Advanced Access) for these
   permissions.
5. Pages that belong to a Business portfolio sometimes only appear in
   `/me/accounts` if `business_management` is also granted. Add it in
   Settings → Channels → *Scopes override*:
   `pages_show_list,pages_read_engagement,read_insights,business_management`.

Flow details: dialog `https://www.facebook.com/v26.0/dialog/oauth`
(`response_type=code`, `state`, PKCE `S256`), code exchange
`GET https://graph.facebook.com/v26.0/oauth/access_token` (client secret +
`code_verifier`), then `grant_type=fb_exchange_token` for a **long-lived user
token (~60 days)**, then `/me/accounts` → one VietDub channel **per Page**, each
holding its Page token. Page tokens derived from a long-lived user token do not
expire; if one stops working VietDub re-mints it from the stored user token.
After ~60 days without reconnecting, the user token expires and the channel shows
*needs reconnect*.
Graph version can be changed with `AETHER_FACEBOOK_GRAPH_VERSION`; if Meta ever
rejects the PKCE parameters for your app, set `AETHER_FACEBOOK_PKCE=0`
(state + app secret still protect the flow).

Disconnect: VietDub forgets the Page token. When the **last** Page of a Facebook
login is disconnected it also calls `DELETE /me/permissions` to revoke the grant
(revoking earlier would break the other Pages).

Data: Page `followers_count` (fallback `fan_count`), latest 25 videos (likes and
comments summaries, lifetime views from `video_insights/total_video_views` when
`read_insights` is granted) and latest 25 posts (reactions, comments, shares).
Page-level "views" are the sum of those recent video views — Meta deprecated the
Page impressions metrics in Nov 2025.

## TikTok (TikTok for Developers)

1. <https://developers.tiktok.com/> → **Manage apps → Connect an app**.
2. Platform: **Desktop** (loopback redirects are only allowed for desktop apps).
3. Add product **Login Kit** and scopes `user.info.basic`, `user.info.profile`,
   `user.info.stats`, `video.list` (the last three come with **Display API**
   features).
4. **Login Kit → Redirect URI**: add
   `http://127.0.0.1:<port>/api/channels/oauth/tiktok/callback`. TikTok requires
   a port and supports a wildcard port (`http://127.0.0.1:*/api/channels/oauth/tiktok/callback`),
   which covers both 8386 and 18386. Max 10 URIs, no query string/fragment.
5. Copy **Client key** (client id) and **Client secret** into VietDub.
6. Until the app passes TikTok's review, use the **Sandbox**: add your TikTok
   account as a *target user*. Production use requires submitting the app for
   review (description, demo video, privacy policy / terms URLs).

Flow details: authorize `https://www.tiktok.com/v2/auth/authorize/` with
`client_key`, comma-separated `scope`, `state`, `code_challenge` and
`code_challenge_method=S256`. **Note:** TikTok's desktop guide specifies the
challenge as the **hex-encoded** SHA-256 of the verifier (not base64url as in
RFC 7636); VietDub follows TikTok's documentation. Token/refresh:
`POST https://open.tiktokapis.com/v2/oauth/token/` (form-encoded, includes
`code_verifier`); access tokens last 24 h, refresh tokens 365 days and may be
rotated on refresh (VietDub stores the new one). Revoke:
`POST https://open.tiktokapis.com/v2/oauth/revoke/`.

Data: `GET /v2/user/info/` (profile + `follower_count`, `likes_count`,
`video_count`) and `POST /v2/video/list/` (up to 60 most recent videos: views,
likes, comments, shares). TikTok has no lifetime "views" total, so the channel
"views" figure is the sum over those recent videos.

---

## Manual channels and CSV import

`POST /api/channels/manual` creates a channel without OAuth. Metrics can be added
per day (`POST /api/channels/{id}/snapshots`) or imported from CSV
(`POST /api/channels/{id}/import`, multipart `file`, `kind=snapshots|videos`).

Snapshot CSV (comma, semicolon or tab separated; header names are flexible):

```csv
date,followers,views,video_count,likes,comments,shares
2026-10-01,12000,1500000,120,,,
02/10/2026,12100,1510000,121,,,
```

Video CSV: `id,title,published_at,views,likes,comments,shares,duration,thumbnail_url,url`
(`duration` may be seconds or `mm:ss`). Numbers accept `1,234`, `1.234`, `1.2K`,
`3,4M`, `2 triệu`. Dates accept `YYYY-MM-DD` and `DD/MM/YYYY`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `redirect_uri_mismatch` (Google) | Client type is not *Desktop app*, or a custom redirect base is set but not registered. |
| "URL blocked" / "Can't load URL" (Facebook) | Redirect URI not in *Valid OAuth Redirect URIs*, or app is Live and the URI is `http` — use an https tunnel. |
| `redirect_uri` error (TikTok) | App platform is not Desktop, or the URI/port isn't registered (use the `*` port). |
| Channel shows *needs reconnect* | Token revoked/expired (Google *Testing* apps: every 7 days; Facebook: 60 days). Click Connect again — the existing channel row and its history are reused. |
| Facebook: "no Pages found" | Select the Pages in the login dialog; for Business-portfolio Pages add `business_management`. |
| YouTube "quota exceeded" | Daily Data API quota reached; sync retries next time. |

from __future__ import annotations

import base64
import hashlib
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.models import Channel, ChannelOAuthState
from app.services.channels import credentials, crypto, oauth
from app.services.channels.providers import get_provider
from app.services.channels.providers.base import ChannelAuthError, pkce_challenge_s256, utcnow
from test_channels_support import (  # noqa: F401  (fixture import)
    GOOGLE_TOKEN,
    TT_TOKEN,
    channels_env,
    configure,
    connect,
    form,
    mock_facebook,
    mock_tiktok,
    mock_youtube,
    query,
)


def _authorize_params(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


# -- PKCE / state -------------------------------------------------------------------------
def test_pkce_s256_matches_rfc7636_vector():
    # RFC 7636 Appendix B.
    assert pkce_challenge_s256("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_code_verifier_and_state_are_random_and_rfc_compliant():
    verifiers = {oauth.new_code_verifier() for _ in range(50)}
    assert len(verifiers) == 50
    for verifier in verifiers:
        assert 43 <= len(verifier) <= 128
        assert re.fullmatch(r"[A-Za-z0-9\-._~]+", verifier)
    assert len({oauth.new_state() for _ in range(50)}) == 50


def test_tiktok_uses_hex_challenge_others_base64url():
    verifier = "a" * 64
    assert get_provider("tiktok").pkce_challenge(verifier) == hashlib.sha256(verifier.encode()).hexdigest()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert get_provider("youtube").pkce_challenge(verifier) == expected
    assert get_provider("facebook").pkce_challenge(verifier) == expected


# -- Credentials --------------------------------------------------------------------------
def test_credentials_masked_encrypted_and_env_fallback(channels_env, monkeypatch):
    client = channels_env["client"]
    view = configure(client, "youtube", secret="GOCSPX-abcdefgh1234")
    assert view["client_secret_set"] is True
    assert view["client_secret_masked"] == "••••••1234"
    assert "GOCSPX-abcdefgh1234" not in client.get("/api/channels/settings").text
    raw = credentials.config_path().read_text()
    assert "GOCSPX" not in raw and "fernet:" in raw
    # Blank secret keeps the stored one (write-only field).
    client.put("/api/channels/credentials/youtube", json={"client_id": "new-id", "client_secret": ""})
    assert credentials.get_credentials("youtube").client_secret == "GOCSPX-abcdefgh1234"
    assert credentials.get_credentials("youtube").client_id == "new-id"
    # Env fallback for unset platforms.
    monkeypatch.setenv("AETHER_TIKTOK_CLIENT_KEY", "tt-key")
    monkeypatch.setenv("AETHER_TIKTOK_CLIENT_SECRET", "tt-secret")
    tiktok = next(p for p in client.get("/api/channels/settings").json()["platforms"] if p["platform"] == "tiktok")
    assert tiktok["client_id"] == "tt-key" and tiktok["client_id_source"] == "env"
    assert tiktok["configured"] is True and tiktok["client_secret_source"] == "env"
    assert tiktok["redirect_uri"] == "http://127.0.0.1:8386/api/channels/oauth/tiktok/callback"
    # Clearing.
    client.put("/api/channels/credentials/youtube", json={"clear_secret": True})
    assert credentials.get_credentials("youtube").client_secret == ""


def test_redirect_base_url_setting(channels_env):
    client = channels_env["client"]
    assert client.put("/api/channels/settings", json={"redirect_base_url": "ftp://x"}).status_code == 400
    data = client.put("/api/channels/settings", json={"redirect_base_url": "https://dub.example.com/"}).json()
    assert data["redirect_base_url"] == "https://dub.example.com"
    fb = next(p for p in data["platforms"] if p["platform"] == "facebook")
    assert fb["redirect_uri"] == "https://dub.example.com/api/channels/oauth/facebook/callback"


def test_token_encryption_round_trip(channels_env):
    secret = "ya29.very-secret-token"
    encrypted = crypto.encrypt_secret(secret)
    assert encrypted.startswith("fernet:") and secret not in encrypted
    assert crypto.decrypt_secret(encrypted) == secret
    assert crypto.encrypt_secret("") == ""
    key_file = channels_env["tmp"] / "storage" / "secrets" / "channels.key"
    assert key_file.exists() and (key_file.stat().st_mode & 0o777) == 0o600
    # A different key cannot decrypt.
    key_file.write_bytes(crypto.Fernet.generate_key())
    crypto.reset_cache_for_tests()
    with pytest.raises(crypto.SecretDecryptionError):
        crypto.decrypt_secret(encrypted)


# -- Start ----------------------------------------------------------------------------------
def test_start_requires_credentials(channels_env):
    response = channels_env["client"].post("/api/channels/oauth/youtube/start")
    assert response.status_code == 400
    assert channels_env["client"].post("/api/channels/oauth/myspace/start").status_code == 404


@pytest.mark.parametrize(
    "platform,host,expected",
    [
        ("youtube", "accounts.google.com", {"client_id": "cid-123", "code_challenge_method": "S256",
                                            "access_type": "offline", "response_type": "code"}),
        ("facebook", "www.facebook.com", {"client_id": "cid-123", "response_type": "code",
                                          "scope": "pages_show_list,pages_read_engagement,read_insights,pages_manage_posts"}),
        ("tiktok", "www.tiktok.com", {"client_key": "cid-123", "response_type": "code", "code_challenge_method": "S256",
                                      "scope": "user.info.basic,user.info.profile,user.info.stats,video.list"}),
    ],
)
def test_start_builds_authorize_url(channels_env, platform, host, expected):
    client = channels_env["client"]
    configure(client, platform)
    data = client.post(f"/api/channels/oauth/{platform}/start").json()
    url = urlsplit(data["authorize_url"])
    params = _authorize_params(data["authorize_url"])
    assert url.scheme == "https" and url.hostname == host
    assert params["state"] == data["state"]
    loopback = "localhost" if platform == "facebook" else "127.0.0.1"
    assert params["redirect_uri"] == f"http://{loopback}:8386/api/channels/oauth/{platform}/callback"
    assert params["code_challenge"]
    for key, value in expected.items():
        assert params[key] == value
    assert "super-secret-value" not in data["authorize_url"]
    with channels_env["Session"]() as db:
        row = db.get(ChannelOAuthState, data["state"])
        verifier = crypto.decrypt_secret(row.code_verifier_encrypted)
        assert get_provider(platform).pkce_challenge(verifier) == params["code_challenge"]


# -- Callback ---------------------------------------------------------------------------------
def test_youtube_callback_exchanges_code_and_stores_encrypted_tokens(channels_env):
    mock_youtube(channels_env["mock"])
    result = connect(channels_env, "youtube")
    assert "Đã kết nối" in result["callback"].text
    status = result["status"]
    assert status["status"] == "connected" and len(status["channels"]) == 1
    token_request = channels_env["mock"].find("POST", "oauth2.googleapis.com/token")[0]
    sent = form(token_request)
    assert sent["grant_type"] == "authorization_code" and sent["code"] == "auth-code"
    assert sent["redirect_uri"] == result["start"]["redirect_uri"]
    assert pkce_challenge_s256(sent["code_verifier"]) == _authorize_params(result["start"]["authorize_url"])["code_challenge"]
    with channels_env["Session"]() as db:
        channel = db.query(Channel).one()
        assert channel.external_id == "UCabc123" and channel.handle == "@vietdub"
        assert GOOGLE_TOKEN["access_token"] not in channel.access_token_encrypted
        assert crypto.decrypt_secret(channel.access_token_encrypted) == GOOGLE_TOKEN["access_token"]
        assert crypto.decrypt_secret(channel.refresh_token_encrypted) == GOOGLE_TOKEN["refresh_token"]
        assert channel.token_expires_at > utcnow() + timedelta(minutes=55)
        assert channel.last_synced_at is not None  # background sync ran after connect
    api_channel = channels_env["client"].get(f"/api/channels/{status['channels'][0]['id']}").json()
    assert "access_token" not in str(api_channel) and "ya29" not in str(api_channel)


def test_facebook_callback_creates_one_channel_per_page(channels_env):
    mock_facebook(channels_env["mock"])
    result = connect(channels_env, "facebook")
    assert result["status"]["status"] == "connected"
    assert len(result["status"]["channel_ids"]) == 2
    exchanges = channels_env["mock"].find("GET", "/oauth/access_token")
    first, second = query(exchanges[0]), query(exchanges[1])
    assert first["code"] == "auth-code" and first["client_secret"] == "super-secret-value" and first["code_verifier"]
    assert second["grant_type"] == "fb_exchange_token" and second["fb_exchange_token"] == "fb-short"
    with channels_env["Session"]() as db:
        pages = {c.external_id: c for c in db.query(Channel)}
        assert crypto.decrypt_secret(pages["p1"].access_token_encrypted) == "page-token-1"
        assert crypto.decrypt_secret(pages["p2"].refresh_token_encrypted) == "fb-long-user"
        assert pages["p1"].account_external_id == "10001"
        assert pages["p1"].token_expires_at is None  # page tokens from long-lived user tokens don't expire
        assert pages["p1"].handle == "@vietdubpage"


def test_tiktok_callback_sends_client_key_and_verifier(channels_env):
    mock_tiktok(channels_env["mock"])
    result = connect(channels_env, "tiktok")
    assert result["status"]["status"] == "connected"
    sent = form(channels_env["mock"].find("POST", "/v2/oauth/token/")[0])
    assert sent["client_key"] == "cid-123" and sent["client_secret"] == "super-secret-value"
    assert sent["grant_type"] == "authorization_code" and len(sent["code_verifier"]) >= 43
    with channels_env["Session"]() as db:
        channel = db.query(Channel).one()
        assert channel.external_id == TT_TOKEN["open_id"]
        assert channel.handle == "@vietdub.tt" and channel.refresh_expires_at is not None


def test_callback_rejects_bad_state(channels_env):
    client = channels_env["client"]
    mock_youtube(channels_env["mock"])
    configure(client, "youtube")
    response = client.get("/api/channels/oauth/youtube/callback", params={"state": "forged", "code": "x"})
    assert response.status_code == 400 and "Kết nối không thành công" in response.text
    start = client.post("/api/channels/oauth/youtube/start").json()
    # Platform mismatch.
    assert client.get("/api/channels/oauth/tiktok/callback", params={"state": start["state"], "code": "x"}).status_code == 400
    # Success then replay.
    assert client.get("/api/channels/oauth/youtube/callback", params={"state": start["state"], "code": "x"}).status_code == 200
    replay = client.get("/api/channels/oauth/youtube/callback", params={"state": start["state"], "code": "x"})
    assert replay.status_code == 400 and "already used" in replay.text
    assert len(channels_env["mock"].find("POST", "oauth2.googleapis.com/token")) == 1


def test_callback_expired_state_and_user_denied(channels_env):
    client = channels_env["client"]
    configure(client, "youtube")
    start = client.post("/api/channels/oauth/youtube/start").json()
    with channels_env["Session"]() as db:
        db.get(ChannelOAuthState, start["state"]).expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    expired = client.get("/api/channels/oauth/youtube/callback", params={"state": start["state"], "code": "c"})
    assert expired.status_code == 400
    assert client.get(f"/api/channels/oauth/status/{start['state']}").json()["status"] == "error"

    start2 = client.post("/api/channels/oauth/youtube/start").json()
    denied = client.get(
        "/api/channels/oauth/youtube/callback", params={"state": start2["state"], "error": "access_denied"}
    )
    assert denied.status_code == 400
    status = client.get(f"/api/channels/oauth/status/{start2['state']}").json()
    assert status["status"] == "error" and status["error"] == "Access was denied."


def test_callback_records_token_endpoint_failure(channels_env):
    client = channels_env["client"]
    channels_env["mock"].on(
        "POST", "https://oauth2.googleapis.com/token", {"error": "invalid_grant", "error_description": "Bad code"}, status=400
    )
    configure(client, "youtube")
    start = client.post("/api/channels/oauth/youtube/start").json()
    response = client.get("/api/channels/oauth/youtube/callback", params={"state": start["state"], "code": "c"})
    assert response.status_code == 400
    status = client.get(f"/api/channels/oauth/status/{start['state']}").json()
    assert status["status"] == "error" and "invalid_grant" in status["error"]
    assert "super-secret-value" not in status["error"]


def test_status_pending_then_unknown(channels_env):
    client = channels_env["client"]
    configure(client, "tiktok")
    start = client.post("/api/channels/oauth/tiktok/start").json()
    assert client.get(f"/api/channels/oauth/status/{start['state']}").json()["status"] == "pending"
    assert client.get("/api/channels/oauth/status/nope").status_code == 404


# -- Refresh ------------------------------------------------------------------------------------
def _expire(env, platform):
    with env["Session"]() as db:
        channel = db.query(Channel).filter(Channel.platform == platform).first()
        channel.token_expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
        return channel.id


def test_youtube_refresh_keeps_refresh_token(channels_env):
    mock = channels_env["mock"]
    mock_youtube(mock)
    connect(channels_env, "youtube")
    channel_id = _expire(channels_env, "youtube")
    mock.on("POST", "https://oauth2.googleapis.com/token", {"access_token": "ya29.new", "expires_in": 3599, "token_type": "Bearer"})
    with channels_env["Session"]() as db:
        channel = db.get(Channel, channel_id)
        token = oauth.ensure_access_token(db, channel, get_provider("youtube"))
        assert token == "ya29.new"
        sent = form(mock.find("POST", "oauth2.googleapis.com/token")[-1])
        assert sent["grant_type"] == "refresh_token" and sent["refresh_token"] == "1//google-refresh"
        db.refresh(channel)
        assert crypto.decrypt_secret(channel.refresh_token_encrypted) == "1//google-refresh"
        assert channel.token_expires_at > utcnow()


def test_tiktok_refresh_rotates_refresh_token(channels_env):
    mock = channels_env["mock"]
    mock_tiktok(mock)
    connect(channels_env, "tiktok")
    channel_id = _expire(channels_env, "tiktok")
    mock.on("POST", "https://open.tiktokapis.com/v2/oauth/token/", {**TT_TOKEN, "access_token": "act.new", "refresh_token": "rft.new"})
    with channels_env["Session"]() as db:
        channel = db.get(Channel, channel_id)
        assert oauth.ensure_access_token(db, channel, get_provider("tiktok")) == "act.new"
        db.refresh(channel)
        assert crypto.decrypt_secret(channel.refresh_token_encrypted) == "rft.new"


def test_refresh_invalid_grant_marks_needs_reauth(channels_env):
    mock = channels_env["mock"]
    mock_youtube(mock)
    connect(channels_env, "youtube")
    channel_id = _expire(channels_env, "youtube")
    mock.on("POST", "https://oauth2.googleapis.com/token", {"error": "invalid_grant"}, status=400)
    with channels_env["Session"]() as db:
        channel = db.get(Channel, channel_id)
        with pytest.raises(ChannelAuthError):
            oauth.ensure_access_token(db, channel, get_provider("youtube"))
        db.refresh(channel)
        assert channel.status == "needs_reauth"


def test_facebook_refresh_remints_page_token(channels_env):
    mock = channels_env["mock"]
    mock_facebook(mock)
    connect(channels_env, "facebook")
    mock.on("GET", "https://graph.facebook.com/v26.0/p1", {"id": "p1", "access_token": "page-token-new"})
    with channels_env["Session"]() as db:
        channel = db.query(Channel).filter(Channel.external_id == "p1").one()
        assert oauth.refresh_channel_token(db, channel, get_provider("facebook")) == "page-token-new"
        request = mock.find("GET", "/v26.0/p1?")[-1]
        assert request.headers["Authorization"] == "Bearer fb-long-user"


# -- Disconnect -------------------------------------------------------------------------------
def test_disconnect_revokes_and_wipes_tokens(channels_env):
    mock = channels_env["mock"]
    mock_tiktok(mock)
    connect(channels_env, "tiktok")
    client = channels_env["client"]
    channel_id = client.get("/api/channels").json()[0]["id"]
    data = client.post(f"/api/channels/{channel_id}/disconnect").json()
    assert data["revoked"] is True and data["channel"]["status"] == "disconnected"
    sent = form(mock.find("POST", "/v2/oauth/revoke/")[0])
    assert sent["token"] == "act.tiktok-access" and sent["client_key"] == "cid-123"
    with channels_env["Session"]() as db:
        channel = db.get(Channel, channel_id)
        assert channel.access_token_encrypted == "" and channel.refresh_token_encrypted == ""
    assert client.post(f"/api/channels/{channel_id}/sync").status_code == 409


def test_facebook_disconnect_only_revokes_last_page(channels_env):
    mock = channels_env["mock"]
    mock_facebook(mock)
    connect(channels_env, "facebook")
    client = channels_env["client"]
    ids = {c["external_id"]: c["id"] for c in client.get("/api/channels").json()}
    first = client.post(f"/api/channels/{ids['p1']}/disconnect").json()
    assert first["revoked"] is False and not mock.find("DELETE", "/me/permissions")
    second = client.delete(f"/api/channels/{ids['p2']}").json()
    assert second["deleted"] is True and second["revoked"] is True
    delete = mock.find("DELETE", "/me/permissions")[0]
    assert delete.headers["Authorization"] == "Bearer fb-long-user"
    assert client.get(f"/api/channels/{ids['p2']}").status_code == 404


def test_revoke_failure_still_disconnects(channels_env):
    mock = channels_env["mock"]
    mock_youtube(mock)
    connect(channels_env, "youtube")
    mock.on("POST", "https://oauth2.googleapis.com/revoke", lambda r: httpx.Response(503, text="down"))
    client = channels_env["client"]
    channel_id = client.get("/api/channels").json()[0]["id"]
    data = client.post(f"/api/channels/{channel_id}/disconnect").json()
    assert data["revoked"] is False and data["revoke_error"]
    assert data["channel"]["status"] == "disconnected"


def test_reconnect_reuses_channel_row(channels_env):
    mock_youtube(channels_env["mock"])
    connect(channels_env, "youtube")
    connect(channels_env, "youtube")
    with channels_env["Session"]() as db:
        assert db.query(Channel).count() == 1

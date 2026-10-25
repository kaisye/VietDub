from app.services import downloader

BOT_ERROR = RuntimeError(
    "ERROR: [youtube] 8YEdm-ZTgT4: Sign in to confirm you’re not a bot. Use --cookies-from-browser"
)


def test_bot_check_without_cookies_points_to_settings(monkeypatch) -> None:
    monkeypatch.delenv("AETHER_YTDLP_COOKIES_FROM_BROWSER", raising=False)
    monkeypatch.delenv("AETHER_YTDLP_COOKIES_FILE", raising=False)

    assert downloader._is_bot_check_error(BOT_ERROR)
    assert "Cấu hình > Tải video" in downloader._bot_check_message(BOT_ERROR)


def test_bot_check_with_cookies_suggests_refreshing_them(monkeypatch) -> None:
    monkeypatch.setenv("AETHER_YTDLP_COOKIES_FROM_BROWSER", "chrome")

    assert downloader._ytdlp_cookie_options("https://www.youtube.com/watch?v=x") == {
        "cookiesfrombrowser": ("chrome", None, None, None)
    }
    assert "hết hạn" in downloader._bot_check_message(BOT_ERROR)


def test_other_errors_are_not_bot_checks() -> None:
    assert not downloader._is_bot_check_error(RuntimeError("HTTP Error 404: Not Found"))

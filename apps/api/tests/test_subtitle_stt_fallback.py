from pathlib import Path

import pytest

from app.services import subtitle


def _force_speech_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subtitle, "_stt_fallback_enabled", lambda: True)
    monkeypatch.setattr(subtitle, "_nvidia_configured", lambda: True)
    monkeypatch.setattr(subtitle, "_groq_stt_enabled", lambda: True)


def test_permission_denied_from_riva_falls_back_to_groq(monkeypatch, tmp_path):
    video = tmp_path / "video.mp4"
    expected = tmp_path / "video.vi.groq.srt"
    _force_speech_path(monkeypatch)
    monkeypatch.setattr(subtitle, "_groq_stt_configured", lambda: True)
    monkeypatch.setattr(
        subtitle,
        "_generate_subtitle_with_nvidia_whisper",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("StatusCode.PERMISSION_DENIED: Authorization failed")
        ),
    )
    monkeypatch.setattr(
        subtitle,
        "_generate_subtitle_with_groq_whisper",
        lambda *_args, **_kwargs: expected,
    )

    result = subtitle.extract_subtitle(video, target_language="VI", subtitle_strategy="speech")

    assert result == expected


def test_permission_denied_does_not_retry_same_nvidia_key_for_brief(monkeypatch, tmp_path):
    video = tmp_path / "video.mp4"
    _force_speech_path(monkeypatch)
    monkeypatch.setattr(subtitle, "_groq_stt_configured", lambda: False)
    monkeypatch.setattr(
        subtitle,
        "_generate_subtitle_with_nvidia_whisper",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("rpc code = PermissionDenied desc = Authorization failed")
        ),
    )
    brief_fallback_called = False

    def fail_if_called(*_args, **_kwargs) -> Path:
        nonlocal brief_fallback_called
        brief_fallback_called = True
        raise AssertionError("must not reuse an unauthorized NVIDIA key")

    monkeypatch.setattr(subtitle, "_generate_subtitle_with_nvidia", fail_if_called)

    with pytest.raises(RuntimeError, match="PERMISSION_DENIED"):
        subtitle.extract_subtitle(
            video,
            brief="A sufficiently long brief that would normally trigger LLM fallback.",
            target_language="VI",
            subtitle_strategy="speech",
        )

    assert brief_fallback_called is False


@pytest.mark.parametrize(
    "message",
    [
        "StatusCode.PERMISSION_DENIED",
        "PermissionDenied: Authorization failed",
        "StatusCode.UNAUTHENTICATED",
        "invalid API key",
    ],
)
def test_riva_authorization_error_detection(message):
    assert subtitle._is_riva_authorization_error(RuntimeError(message))

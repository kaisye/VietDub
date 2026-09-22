from __future__ import annotations

import json
import sys
import types
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import httpx
from app.services import production_configuration, tts, zerotts_tts
from app.services.production_configuration import migrate_configuration_document
from app.services.runtime_settings import RuntimeSettings, get_runtime_settings
from app.services.tts_runtime import RUNTIME_ZEROTTS, resolve_effective_tts_runtime
from app.services.voice_options import VoiceOption, canonical_voice_id, list_voice_options


def test_zerotts_presets_are_exposed_in_voice_catalog() -> None:
    voices = {voice.id: voice for voice in list_voice_options()}

    assert len(zerotts_tts.ZEROTTS_PRESETS) == 8
    assert voices["zerotts_maichi"].engine == "zerotts"
    assert voices["zerotts_tiendat"].name == "Tiến Đạt"


def test_zerotts_provider_resolves_without_gpu_probes() -> None:
    settings = RuntimeSettings(tts_provider="zerotts")

    assert resolve_effective_tts_runtime(settings) == RUNTIME_ZEROTTS


def test_zerotts_uses_two_threads_by_default_on_apple_silicon(monkeypatch) -> None:
    monkeypatch.delenv("AETHER_ZEROTTS_THREADS", raising=False)
    monkeypatch.setattr(zerotts_tts.sys, "platform", "darwin")
    monkeypatch.setattr(zerotts_tts.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(zerotts_tts.os, "cpu_count", lambda: 8)
    assert zerotts_tts._thread_count() == 2

    monkeypatch.setenv("AETHER_ZEROTTS_THREADS", "4")
    assert zerotts_tts._thread_count() == 4


def test_removed_nghitts_settings_and_voice_ids_migrate(tmp_path: Path, monkeypatch) -> None:
    settings_path = tmp_path / "runtime-settings.json"
    settings_path.write_text(json.dumps({"tts_provider": "nghitts"}), encoding="utf-8")
    monkeypatch.setenv("AETHER_RUNTIME_SETTINGS_PATH", str(settings_path))

    assert get_runtime_settings().tts_provider == "zerotts"
    assert canonical_voice_id("nghitts_minhquang") == "zerotts_quangminh"
    migrated = migrate_configuration_document(
        {"voice": {"voice_id": "nghitts_minhquang", "provider": "nghitts"}}
    )
    assert migrated["voice"] == {
        "voice_id": "zerotts_quangminh",
        "provider": "zerotts",
    }


def test_provider_dispatches_to_zerotts(tmp_path: Path) -> None:
    output = tmp_path / "voice.wav"

    with (
        patch("app.services.tts.synthesize_zerotts") as synthesize,
        tts.tts_provider_override("zerotts"),
    ):
        tts._save_provider_tts("Xin chào", "zerotts_maichi", output, "+0%")

    synthesize.assert_called_once_with("Xin chào", "zerotts_maichi", output, "+0%")


def test_community_preview_does_not_route_voice_to_omnivoice(monkeypatch) -> None:
    voice = VoiceOption(
        id="zerotts_community_test",
        name="Community Test",
        locale="vi-VN",
        language="Vietnamese",
        type="ZeroTTS Community",
        reference_audio_path="/tmp/community-preview.wav",
        engine="zerotts",
    )
    monkeypatch.setattr(
        production_configuration,
        "list_voice_options",
        lambda: [voice],
    )
    configuration = {"voice": {"voice_id": voice.id}}

    production_configuration._snapshot_voice(configuration)

    assert configuration["voice"]["provider"] == "zerotts"
    assert configuration["voice"]["mode"] == ""
    assert configuration["voice"]["reference"] == {}


def test_synthesize_normalizes_chunks_and_writes_wav(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    class FakeModel:
        sample_rate = 48_000

        def synthesize(self, text: str, voice: str):
            calls.append((text, voice))
            return np.array([[0.1, -0.1]], dtype=np.float32)

        def save_audio(self, audio, path: str) -> None:
            Path(path).write_bytes(b"RIFF-test")

    fake_package = types.ModuleType("zerotts")
    fake_package.normalize_vi_text = lambda text: f"normalized {text}"
    fake_chunking = types.ModuleType("zerotts.chunking")
    fake_chunking.normalize_punctuation = lambda text: text
    fake_chunking.chunk_text = lambda text, max_chunk_sec: ["đoạn một", "đoạn hai"]
    fake_chunking.clean_segment_punctuation = lambda text: f"{text}."
    monkeypatch.setitem(sys.modules, "zerotts", fake_package)
    monkeypatch.setitem(sys.modules, "zerotts.chunking", fake_chunking)
    monkeypatch.setattr(zerotts_tts, "_MODEL", FakeModel())

    output = zerotts_tts.synthesize_zerotts(
        "Ngày 15/9/2026", "zerotts_maichi", tmp_path / "sample.wav"
    )

    assert output.read_bytes() == b"RIFF-test"
    assert calls == [
        ("đoạn một.", "maichi"),
        ("đoạn hai.", "maichi"),
    ]


def test_community_voice_pack_install_list_route_and_delete(
    tmp_path: Path, monkeypatch
) -> None:
    voice_root = tmp_path / "voices"
    voice_root.mkdir()
    monkeypatch.setattr(zerotts_tts, "_community_voice_root", lambda: voice_root)
    monkeypatch.setattr(zerotts_tts, "_MODEL", object())

    pack_dir = tmp_path / "pack" / "congdong"
    pack_dir.mkdir(parents=True)
    np.savez(
        pack_dir / "voice.npz",
        n_voice_queries=np.asarray(10, dtype=np.int64),
        voice_emb=np.zeros((1, 10, 768), dtype=np.float32),
    )
    (pack_dir / "meta.json").write_text(
        json.dumps(
            {
                "display_name": "Giọng Cộng Đồng",
                "language": "vi",
                "gender": "nữ",
                "tags": ["trẻ", "ấm áp"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (pack_dir / "preview.wav").write_bytes(b"RIFF-preview")
    archive = tmp_path / "community.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for source in pack_dir.iterdir():
            output.write(source, f"congdong/{source.name}")

    installed = zerotts_tts.install_zerotts_community_archive(archive)

    assert len(installed) == 1
    voice = installed[0]
    assert voice.display_name == "Giọng Cộng Đồng"
    assert zerotts_tts.is_zerotts_voice(voice.id)
    assert zerotts_tts._upstream_voice_id(voice.id) == "congdong"
    option = next(item for item in list_voice_options() if item.id == voice.id)
    assert option.engine == "zerotts"
    assert option.removable is True
    assert option.reference_audio_path.endswith("preview.wav")

    assert zerotts_tts.delete_zerotts_community_voice(voice.id) is True
    assert not voice_root.joinpath("congdong").exists()


def test_catalog_voice_download_is_converted_and_installed(
    tmp_path: Path, monkeypatch
) -> None:
    voice_root = tmp_path / "voices"
    voice_root.mkdir()
    monkeypatch.setattr(zerotts_tts, "_community_voice_root", lambda: voice_root)
    monkeypatch.setattr(zerotts_tts, "_MODEL", object())
    record = {
        "voice_id": "CommunityVoice123",
        "name": "Ngọc Huyền",
        "description": "Giọng cộng đồng",
        "type": "clone",
        "tags": ["female"],
        "language": "vi",
        "status": "completed",
        "voice_url": "https://cdn.zeroweight.ai/voice.bin",
        "preview_url": "https://cdn.zeroweight.ai/preview.wav",
    }

    def fake_get(url, **kwargs):
        return httpx.Response(
            200,
            json={"items": [record]},
            request=httpx.Request("GET", str(url)),
        )

    voice_bytes = np.zeros((1, 10, 768), dtype="<f4").tobytes()
    monkeypatch.setattr(zerotts_tts.httpx, "get", fake_get)
    monkeypatch.setattr(
        zerotts_tts,
        "_download_community_file",
        lambda url, limit: b"RIFF-preview" if url.endswith(".wav") else voice_bytes,
    )

    catalog = zerotts_tts.fetch_zerotts_community_catalog()
    assert catalog[0]["name"] == "Ngọc Huyền"
    assert catalog[0]["installed"] is False

    installed = zerotts_tts.install_zerotts_catalog_voice(record["voice_id"])
    assert installed.display_name == "Ngọc Huyền"
    assert installed.preview_path.endswith("preview.wav")

    refreshed = zerotts_tts.fetch_zerotts_community_catalog()
    assert refreshed[0]["installed"] is True
    assert refreshed[0]["installed_voice_id"] == installed.id


def test_featured_community_voices_are_first_in_catalog(monkeypatch) -> None:
    records = [
        {
            "voice_id": "another-community-voice",
            "name": "Giọng Khác",
            "status": "completed",
        },
        {
            "voice_id": "MAJfDVfQqPF5Fr4DvibN",
            "name": "Thức Dậy Đi",
            "status": "completed",
        },
        {
            "voice_id": "7owuK1LaOPOaQjeSzmQ4",
            "name": "Ngọc Huyền",
            "status": "completed",
        },
    ]

    def fake_get(url, **kwargs):
        return httpx.Response(
            200,
            json={"items": records},
            request=httpx.Request("GET", str(url)),
        )

    monkeypatch.setattr(zerotts_tts.httpx, "get", fake_get)
    monkeypatch.setattr(zerotts_tts, "list_zerotts_community_voices", lambda: [])

    catalog = zerotts_tts.fetch_zerotts_community_catalog()

    assert [voice["name"] for voice in catalog] == [
        "Ngọc Huyền",
        "Thức Dậy Đi",
        "Giọng Khác",
    ]

"""Music library API: local assets shared by all projects, Openverse / Jamendo search + import,
and the per-project credits text for the editor's audio tracks."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from .services import editor, music_library
from .services.music_library import LibraryError, ProviderError

router = APIRouter(tags=["music-library"])


class AssetPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    artist: str | None = Field(default=None, max_length=200)
    tags: list[str] | None = Field(default=None, max_length=20)
    kind: Literal["music", "sfx"] | None = None
    attribution: str | None = Field(default=None, max_length=1000)


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jamendo_client_id: str | None = Field(default=None, max_length=64)


class ImportRequest(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    kind: Literal["music", "sfx"] | None = None


def _provider_error(exc: LibraryError) -> HTTPException:
    status = exc.status_code if isinstance(exc, ProviderError) else 400
    return HTTPException(status_code=status, detail=str(exc))


@router.get("/music-library")
def list_music(q: str = "", tag: str = "", kind: str = "") -> dict[str, Any]:
    return music_library.list_assets(q, tag, kind)


@router.post("/music-library/upload")
def upload_music(files: list[UploadFile] = File(...), kind: str = Form("music")) -> dict[str, Any]:
    imported, errors = [], []
    for upload in files[:50]:
        try:
            asset = music_library.import_upload(upload.file, upload.filename or "audio", kind)
            imported.append(music_library.public(asset))
        except LibraryError as exc:
            errors.append({"filename": upload.filename, "error": str(exc)})
    if not imported and errors:
        raise HTTPException(status_code=400, detail=errors[0]["error"])
    return {"items": imported, "errors": errors}


@router.get("/music-library/settings")
def get_music_settings() -> dict[str, Any]:
    return music_library.public_settings()


@router.put("/music-library/settings")
def put_music_settings(payload: SettingsPatch) -> dict[str, Any]:
    try:
        return music_library.save_settings(payload.model_dump(exclude_unset=True))
    except LibraryError as exc:
        raise _provider_error(exc) from exc


@router.get("/music-library/openverse/search")
def search_openverse(
    q: str,
    category: Literal["music", "sound_effect", "any"] = "music",
    commercial_only: bool = True,
    length: str = "",
    page: int = 1,
) -> dict[str, Any]:
    if not q.strip():
        raise HTTPException(status_code=400, detail="Nhập từ khóa để tìm nhạc.")
    try:
        return music_library.openverse_search(
            q, category=category, commercial_only=commercial_only, length=length, page=min(max(page, 1), 50)
        )
    except LibraryError as exc:
        raise _provider_error(exc) from exc


@router.post("/music-library/openverse/import")
def import_openverse(payload: ImportRequest) -> dict[str, Any]:
    try:
        return music_library.public(music_library.openverse_import(payload.id, payload.kind))
    except LibraryError as exc:
        raise _provider_error(exc) from exc


@router.get("/music-library/jamendo/search")
def search_jamendo(q: str, commercial_only: bool = True, page: int = 1) -> dict[str, Any]:
    if not q.strip():
        raise HTTPException(status_code=400, detail="Nhập từ khóa để tìm nhạc.")
    try:
        return music_library.jamendo_search(q, commercial_only=commercial_only, page=min(max(page, 1), 50))
    except LibraryError as exc:
        raise _provider_error(exc) from exc


@router.post("/music-library/jamendo/import")
def import_jamendo(payload: ImportRequest) -> dict[str, Any]:
    try:
        return music_library.public(music_library.jamendo_import(payload.id))
    except LibraryError as exc:
        raise _provider_error(exc) from exc


@router.get("/music-library/{asset_id}/peaks")
def music_peaks(asset_id: str) -> dict[str, Any]:
    try:
        return music_library.peaks(asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Không tìm thấy file nhạc.") from exc


@router.patch("/music-library/{asset_id}")
def patch_music(asset_id: str, payload: AssetPatch) -> dict[str, Any]:
    try:
        return music_library.public(music_library.update_asset(asset_id, payload.model_dump(exclude_unset=True)))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Không tìm thấy file nhạc.") from exc
    except LibraryError as exc:
        raise _provider_error(exc) from exc


@router.delete("/music-library/{asset_id}", status_code=204)
def delete_music(asset_id: str) -> None:
    try:
        music_library.delete_asset(asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Không tìm thấy file nhạc.") from exc


@router.get("/jobs/{job_id}/editor/music-credits")
def music_credits(job_id: str, lang: Literal["vi", "en"] = "vi") -> dict[str, Any]:
    with editor.workspace_lock(job_id):
        workspace = editor.load_workspace(job_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Chưa có workspace editor cho job này.")
    return music_library.project_credits(workspace, lang)

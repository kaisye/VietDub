from __future__ import annotations

import copy
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .database import get_db
from .models import (
    AuditLog,
    MediaJob,
    MediaJobStatus,
    PresetVersion,
    ProductionPreset,
    Project,
    ProjectVideo,
    Video,
    VideoStatus,
)
from .schemas import (
    ConfigurationResolveOut,
    ConfigurationResolveRequest,
    ConfigurationValidationOut,
    CreatedMediaJobOut,
    MediaJobOut,
    ProductionPresetCreate,
    ProductionPresetImport,
    ProductionPresetOut,
    ProductionPresetPatch,
    ProductionPresetVersionOut,
    ProductionVideoOut,
    ProjectCreate,
    ProjectJobCreate,
    ProjectOut,
    ProjectPatch,
    ProjectVideoCreate,
    ProjectVideoPatch,
    QuickVideoJobCreate,
)
from .services.metadata import detect_video_platform, extract_video_metadata
from .services.production_configuration import (
    CONFIGURATION_SCHEMA_VERSION,
    ProductionConfiguration,
    ResolvedConfiguration,
    application_defaults,
    parse_configuration_json,
    resolve_configuration,
    safe_runtime_snapshot,
    sanitize_export,
    stable_json,
    validate_configuration_layer,
    validate_configuration_size,
    validate_runtime_compatibility,
)
from .worker import queue_job


router = APIRouter()
BUILTIN_PRESET_ID = "builtin-default"


def ensure_builtin_production_preset(db: Session) -> None:
    if db.get(ProductionPreset, BUILTIN_PRESET_ID):
        return
    configuration = application_defaults()
    preset = ProductionPreset(
        id=BUILTIN_PRESET_ID,
        name="Aether Studio Default",
        description="Built-in production defaults migrated from the legacy one-off workflow.",
        category="Built-in",
        schema_version=CONFIGURATION_SCHEMA_VERSION,
        current_version=1,
        configuration_json=stable_json(configuration),
    )
    preset.versions.append(
        PresetVersion(
            version=1,
            schema_version=CONFIGURATION_SCHEMA_VERSION,
            configuration_json=stable_json(configuration),
            change_note="Created by the Phase 4 idempotent migration.",
        )
    )
    db.add(preset)
    db.commit()


@router.get("/presets", response_model=list[ProductionPresetOut])
def list_presets(
    include_archived: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> list[ProductionPresetOut]:
    query = db.query(ProductionPreset).order_by(ProductionPreset.updated_at.desc())
    if not include_archived:
        query = query.filter(ProductionPreset.archived_at.is_(None))
    return [_preset_out(item) for item in query.all()]


@router.post("/presets", response_model=ProductionPresetOut)
def create_preset(payload: ProductionPresetCreate, db: Session = Depends(get_db)) -> ProductionPresetOut:
    _ensure_unique_preset_name(db, payload.name)
    configuration = _validated_layer(payload.configuration)
    preset = ProductionPreset(
        name=_display_text(payload.name, 160),
        description=_display_text(payload.description, 5000),
        category=_display_text(payload.category, 80),
        cover_asset_id=_display_text(payload.cover_asset_id, 160) or None,
        schema_version=CONFIGURATION_SCHEMA_VERSION,
        current_version=1,
        configuration_json=stable_json(configuration),
    )
    preset.versions.append(
        PresetVersion(
            version=1,
            schema_version=CONFIGURATION_SCHEMA_VERSION,
            configuration_json=preset.configuration_json,
            change_note=_display_text(payload.change_note, 1000),
        )
    )
    db.add(preset)
    db.flush()
    _audit(db, "production_preset", preset.id, "created", "Production preset created.")
    db.commit()
    db.refresh(preset)
    return _preset_out(preset)


@router.get("/presets/{preset_id}", response_model=ProductionPresetOut)
def get_preset(preset_id: str, db: Session = Depends(get_db)) -> ProductionPresetOut:
    return _preset_out(_require_preset(db, preset_id))


@router.patch("/presets/{preset_id}", response_model=ProductionPresetOut)
def patch_preset(
    preset_id: str,
    payload: ProductionPresetPatch,
    db: Session = Depends(get_db),
) -> ProductionPresetOut:
    preset = _require_preset(db, preset_id)
    values = payload.model_dump(exclude_unset=True)
    if "name" in values and values["name"] != preset.name:
        _ensure_unique_preset_name(db, str(values["name"]), exclude_id=preset.id)
        preset.name = _display_text(values["name"], 160)
    for field, limit in (("description", 5000), ("category", 80), ("cover_asset_id", 160)):
        if field in values:
            cleaned = _display_text(values[field], limit)
            setattr(preset, field, cleaned or None if field == "cover_asset_id" else cleaned)
    if "configuration" in values and values["configuration"] is not None:
        configuration = _validated_layer(values["configuration"])
        preset.current_version += 1
        preset.configuration_json = stable_json(configuration)
        preset.schema_version = CONFIGURATION_SCHEMA_VERSION
        preset.versions.append(
            PresetVersion(
                version=preset.current_version,
                schema_version=CONFIGURATION_SCHEMA_VERSION,
                configuration_json=preset.configuration_json,
                change_note=_display_text(values.get("change_note") or "Updated preset", 1000),
            )
        )
    preset.updated_at = datetime.utcnow()
    _audit(db, "production_preset", preset.id, "updated", f"Preset version {preset.current_version} saved.")
    db.commit()
    db.refresh(preset)
    return _preset_out(preset)


@router.post("/presets/{preset_id}/duplicate", response_model=ProductionPresetOut)
def duplicate_preset(preset_id: str, db: Session = Depends(get_db)) -> ProductionPresetOut:
    source = _require_preset(db, preset_id)
    name = _available_copy_name(db, source.name)
    duplicate = ProductionPreset(
        name=name,
        description=source.description,
        category=source.category,
        cover_asset_id=source.cover_asset_id,
        schema_version=source.schema_version,
        current_version=1,
        configuration_json=source.configuration_json,
    )
    duplicate.versions.append(
        PresetVersion(
            version=1,
            schema_version=source.schema_version,
            configuration_json=source.configuration_json,
            change_note=f"Duplicated from {source.name} version {source.current_version}.",
        )
    )
    db.add(duplicate)
    db.flush()
    _audit(db, "production_preset", duplicate.id, "duplicated", f"Duplicated from preset {source.id}.")
    db.commit()
    db.refresh(duplicate)
    return _preset_out(duplicate)


@router.post("/presets/{preset_id}/archive", response_model=ProductionPresetOut)
def archive_preset(preset_id: str, db: Session = Depends(get_db)) -> ProductionPresetOut:
    preset = _require_preset(db, preset_id)
    preset.archived_at = preset.archived_at or datetime.utcnow()
    preset.updated_at = datetime.utcnow()
    _audit(db, "production_preset", preset.id, "archived", "Production preset archived.")
    db.commit()
    db.refresh(preset)
    return _preset_out(preset)


@router.get("/presets/{preset_id}/export")
def export_preset(preset_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    preset = _require_preset(db, preset_id)
    return sanitize_export(
        {
            "export_version": 1,
            "preset": {
                "name": preset.name,
                "description": preset.description,
                "category": preset.category,
                "schema_version": preset.schema_version,
                "configuration": parse_configuration_json(preset.configuration_json),
            },
        }
    )


@router.post("/presets/import", response_model=ProductionPresetOut)
def import_preset(payload: ProductionPresetImport, db: Session = Depends(get_db)) -> ProductionPresetOut:
    document = sanitize_export(payload.preset)
    if not isinstance(document, dict):
        raise HTTPException(status_code=400, detail="Preset import document must be an object.")
    source = document.get("preset") if isinstance(document.get("preset"), dict) else document
    if not isinstance(source, dict):
        raise HTTPException(status_code=400, detail="Preset import document is missing preset data.")
    _validate_import_values(source)
    name = payload.name or source.get("name") or "Imported preset"
    request = ProductionPresetCreate(
        name=_available_copy_name(db, _display_text(name, 160)),
        description=_display_text(source.get("description"), 5000),
        category=_display_text(source.get("category") or "Imported", 80),
        configuration=source.get("configuration") or {},
        change_note="Imported preset.",
    )
    return create_preset(request, db)


@router.post("/presets/{preset_id}/validate", response_model=ConfigurationValidationOut)
def validate_preset(preset_id: str, db: Session = Depends(get_db)) -> ConfigurationValidationOut:
    preset = _require_preset(db, preset_id)
    try:
        configuration = parse_configuration_json(preset.configuration_json)
        _validate_live_library_references(configuration)
        resolved = resolve_configuration(preset=configuration)
    except ValueError as exc:
        return ConfigurationValidationOut(valid=False, errors=[str(exc)])
    return ConfigurationValidationOut(valid=True, configuration=resolved.configuration.model_dump(mode="json"))


@router.post("/configuration/resolve", response_model=ConfigurationResolveOut)
def resolve_configuration_api(
    payload: ConfigurationResolveRequest,
    db: Session = Depends(get_db),
) -> ConfigurationResolveOut:
    preset, preset_version = _preset_layer(db, payload.preset_id, payload.preset_version)
    project = _require_project(db, payload.project_id) if payload.project_id else None
    if project:
        project_layer = parse_configuration_json(project.defaults_json)
        if not preset and project.origin_preset_id:
            preset_version = project.origin_preset_version
    else:
        project_layer = None
    try:
        resolved = resolve_configuration(
            preset=preset,
            project=project_layer,
            overrides=payload.overrides,
        )
    except ValueError as exc:
        return ConfigurationResolveOut(
            valid=False,
            errors=[str(exc)],
            preset_id=payload.preset_id or (project.origin_preset_id if project else None),
            preset_version=preset_version,
            project_id=project.id if project else None,
        )
    return _resolved_out(
        resolved,
        preset_id=payload.preset_id or (project.origin_preset_id if project else None),
        preset_version=preset_version,
        project_id=project.id if project else None,
    )


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(
    include_archived: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> list[ProjectOut]:
    query = db.query(Project).order_by(Project.updated_at.desc())
    if not include_archived:
        query = query.filter(Project.archived_at.is_(None))
    return [_project_out(item) for item in query.all()]


@router.post("/projects", response_model=ProjectOut)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> ProjectOut:
    preset_layer, preset_version = _preset_layer(db, payload.origin_preset_id, payload.origin_preset_version)
    project_layer = copy.deepcopy(payload.defaults)
    project_layer.setdefault("languages", {})
    project_layer["languages"]["source"] = payload.source_language
    project_layer["languages"]["target"] = payload.target_language
    if payload.glossary:
        project_layer.setdefault("translation", {})["glossary"] = payload.glossary
    _apply_voice_cast(project_layer, payload.voice_cast)
    try:
        resolved = resolve_configuration(preset=preset_layer, project=project_layer)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    project = Project(
        name=_display_text(payload.name, 160),
        description=_display_text(payload.description, 5000),
        cover_asset_id=_display_text(payload.cover_asset_id, 160) or None,
        source_language=resolved.configuration.languages.source,
        target_language=resolved.configuration.languages.target,
        origin_preset_id=payload.origin_preset_id,
        origin_preset_version=preset_version,
        defaults_schema_version=CONFIGURATION_SCHEMA_VERSION,
        defaults_revision=1,
        defaults_json=stable_json(resolved.configuration.model_dump(mode="json")),
        glossary_json=stable_json(payload.glossary),
        voice_cast_json=stable_json(payload.voice_cast),
    )
    db.add(project)
    db.flush()
    _audit(db, "project", project.id, "created", "Project workspace created.")
    db.commit()
    db.refresh(project)
    return _project_out(project)


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, db: Session = Depends(get_db)) -> ProjectOut:
    return _project_out(_require_project(db, project_id))


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def patch_project(project_id: str, payload: ProjectPatch, db: Session = Depends(get_db)) -> ProjectOut:
    project = _require_project(db, project_id)
    values = payload.model_dump(exclude_unset=True)
    for field, limit in (("name", 160), ("description", 5000), ("cover_asset_id", 160)):
        if field in values:
            cleaned = _display_text(values[field], limit)
            setattr(project, field, cleaned or None if field == "cover_asset_id" else cleaned)

    configuration_changed = any(
        key in values for key in ("source_language", "target_language", "defaults", "glossary", "voice_cast")
    )
    if configuration_changed:
        layer = copy.deepcopy(values.get("defaults") or {})
        layer.setdefault("languages", {})
        layer["languages"]["source"] = values.get("source_language", project.source_language)
        layer["languages"]["target"] = values.get("target_language", project.target_language)
        glossary = values.get("glossary", parse_configuration_json(project.glossary_json))
        voice_cast = values.get("voice_cast", parse_configuration_json(project.voice_cast_json))
        layer.setdefault("translation", {})["glossary"] = glossary
        _apply_voice_cast(layer, voice_cast)
        try:
            resolved = resolve_configuration(
                project=parse_configuration_json(project.defaults_json),
                overrides=layer,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        project.defaults_json = stable_json(resolved.configuration.model_dump(mode="json"))
        project.source_language = resolved.configuration.languages.source
        project.target_language = resolved.configuration.languages.target
        project.glossary_json = stable_json(glossary)
        project.voice_cast_json = stable_json(voice_cast)
        project.defaults_revision += 1
    project.updated_at = datetime.utcnow()
    _audit(db, "project", project.id, "updated", f"Project defaults revision {project.defaults_revision} saved.")
    db.commit()
    db.refresh(project)
    return _project_out(project)


@router.post("/projects/{project_id}/archive", response_model=ProjectOut)
def archive_project(project_id: str, db: Session = Depends(get_db)) -> ProjectOut:
    project = _require_project(db, project_id)
    project.archived_at = project.archived_at or datetime.utcnow()
    project.updated_at = datetime.utcnow()
    _audit(db, "project", project.id, "archived", "Project workspace archived.")
    db.commit()
    db.refresh(project)
    return _project_out(project)


@router.get("/projects/{project_id}/videos", response_model=list[ProductionVideoOut])
def list_project_videos(project_id: str, db: Session = Depends(get_db)) -> list[ProductionVideoOut]:
    project = _require_project(db, project_id)
    return [_project_video_out(item) for item in project.video_memberships]


@router.post("/projects/{project_id}/videos", response_model=ProductionVideoOut)
def create_project_video(
    project_id: str,
    payload: ProjectVideoCreate,
    db: Session = Depends(get_db),
) -> ProductionVideoOut:
    project = _require_project(db, project_id)
    try:
        resolved = resolve_configuration(
            project=parse_configuration_json(project.defaults_json),
            overrides=payload.overrides,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    video = _new_video(payload, resolved.configuration)
    db.add(video)
    db.flush()
    sort_order = payload.sort_order
    if sort_order is None:
        sort_order = max((item.sort_order for item in project.video_memberships), default=-1) + 1
    membership = ProjectVideo(
        project_id=project.id,
        video_id=video.id,
        sort_order=sort_order,
        video_overrides_json=stable_json(payload.overrides),
        overrides_revision=1,
    )
    db.add(membership)
    _audit(db, "video", video.id, "created", f"Video added to project {project.id}.")
    db.commit()
    db.refresh(membership)
    return _project_video_out(membership)


@router.patch("/projects/{project_id}/videos/{video_id}", response_model=ProductionVideoOut)
def patch_project_video(
    project_id: str,
    video_id: str,
    payload: ProjectVideoPatch,
    db: Session = Depends(get_db),
) -> ProductionVideoOut:
    membership = _require_project_video(db, project_id, video_id)
    values = payload.model_dump(exclude_unset=True)
    if "overrides" in values and values["overrides"] is not None:
        project = _require_project(db, project_id)
        try:
            resolve_configuration(
                project=parse_configuration_json(project.defaults_json),
                overrides=values["overrides"],
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        membership.video_overrides_json = stable_json(values["overrides"])
        membership.overrides_revision += 1
    if "sort_order" in values:
        membership.sort_order = values["sort_order"]
    membership.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(membership)
    return _project_video_out(membership)


@router.post("/jobs/from-quick-video", response_model=CreatedMediaJobOut)
def create_quick_video_job(
    payload: QuickVideoJobCreate,
    db: Session = Depends(get_db),
) -> CreatedMediaJobOut:
    preset_layer, preset_version = _preset_layer(db, payload.preset_id, payload.preset_version)
    try:
        base = resolve_configuration(preset=preset_layer, overrides=payload.source.overrides)
        resolved = resolve_configuration(
            project=base.configuration.model_dump(mode="json"),
            overrides=payload.overrides,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.run:
        _assert_runtime_compatible(resolved.configuration)
    video = _new_video(payload.source, resolved.configuration)
    db.add(video)
    db.flush()
    job = _new_media_job(
        video=video,
        source=payload.source,
        resolved=resolved,
        preset_id=payload.preset_id,
        preset_version=preset_version,
    )
    db.add(job)
    db.flush()
    _audit(db, "media_job", job.id, "created", "Quick Video job created from resolved configuration.")
    db.commit()
    db.refresh(video)
    db.refresh(job)
    if payload.run:
        job = queue_job(db, job)
    return CreatedMediaJobOut(video=_standalone_video_out(video), job=MediaJobOut.model_validate(job))


@router.post("/projects/{project_id}/jobs", response_model=CreatedMediaJobOut)
def create_project_job(
    project_id: str,
    payload: ProjectJobCreate,
    db: Session = Depends(get_db),
) -> CreatedMediaJobOut:
    project = _require_project(db, project_id)
    membership = _require_project_video(db, project_id, payload.video_id)
    membership_overrides = parse_configuration_json(membership.video_overrides_json)
    base = resolve_configuration(
        project=parse_configuration_json(project.defaults_json),
        overrides=membership_overrides,
    )
    try:
        resolved = resolve_configuration(
            project=base.configuration.model_dump(mode="json"),
            overrides=payload.overrides,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.run:
        _assert_runtime_compatible(resolved.configuration)
    source = ProjectVideoCreate(
        video_url=membership.video.video_url,
        source_title=membership.video.content or None,
        thumbnail_url=membership.video.thumbnail_url,
        content=membership.video.content,
        platform=membership.video.platform,
    )
    job = _new_media_job(
        video=membership.video,
        source=source,
        resolved=resolved,
        project=project,
        preset_id=project.origin_preset_id,
        preset_version=project.origin_preset_version,
    )
    db.add(job)
    db.flush()
    _audit(db, "media_job", job.id, "created", f"Project job created for project {project.id}.")
    db.commit()
    db.refresh(job)
    if payload.run:
        job = queue_job(db, job)
    return CreatedMediaJobOut(video=_project_video_out(membership), job=MediaJobOut.model_validate(job))


def _new_video(source: ProjectVideoCreate, configuration: ProductionConfiguration) -> Video:
    platform = detect_video_platform(source.video_url) or source.platform or configuration.output.platform
    metadata = extract_video_metadata(source.video_url)
    return Video(
        status=VideoStatus.draft,
        video_url=source.video_url,
        thumbnail_url=source.thumbnail_url or metadata.thumbnail_url,
        content=source.source_title or metadata.title or source.content,
        source_language=configuration.languages.source,
        target_language=configuration.languages.target,
        voice=configuration.voice.voice_id,
        platform=platform,
        workflow_template="Resolved Production Configuration",
        progress=0,
    )


def _new_media_job(
    *,
    video: Video,
    source: ProjectVideoCreate,
    resolved: ResolvedConfiguration,
    project: Project | None = None,
    preset_id: str | None = None,
    preset_version: int | None = None,
) -> MediaJob:
    configuration = resolved.configuration
    voice_map = {
        role: {
            "voice": assignment.voice_id,
            "voice_rate": _legacy_rate(assignment.rate),
        }
        for role, assignment in configuration.speakers.voice_map.items()
    }
    return MediaJob(
        video_id=video.id,
        project_id=project.id if project else None,
        preset_id=preset_id,
        preset_version=preset_version,
        configuration_schema_version=configuration.schema_version,
        configuration_snapshot_json=stable_json(configuration.model_dump(mode="json")),
        runtime_snapshot_json=stable_json(safe_runtime_snapshot()),
        video_url=video.video_url,
        source_title=source.source_title or video.content or None,
        thumbnail_url=video.thumbnail_url,
        content=source.content,
        source_language=configuration.languages.source,
        target_language=configuration.languages.target,
        voice=configuration.voice.voice_id,
        voice_rate=_legacy_rate(configuration.voice.rate),
        voice_mode=configuration.voice.mode,
        voice_instruction=configuration.voice.instruction,
        voice_reference_audio_url=(
            configuration.voice.reference.url
            or configuration.voice.reference.path
        ),
        voice_reference_text=(
            configuration.voice.reference.text
            or configuration.voice.reference.text_path
        ),
        speaker_diarization_enabled=configuration.speakers.enabled,
        speaker_min_count=configuration.speakers.min_count,
        speaker_max_count=configuration.speakers.max_count,
        speaker_voice_map=stable_json(voice_map),
        download_quality=configuration.source.download_quality,
        test_clip_seconds=configuration.source.test_clip_seconds,
        render_quality=configuration.output.render_quality,
        render_mode=configuration.output.render_mode,
        platform=configuration.output.platform,
        publish_date=configuration.publish.publish_date,
        publish_time=configuration.publish.publish_time,
        status=MediaJobStatus.draft,
        progress=0,
        current_step="Draft",
    )


def _preset_layer(
    db: Session,
    preset_id: str | None,
    version: int | None,
) -> tuple[dict[str, Any] | None, int | None]:
    if not preset_id:
        return None, None
    preset = _require_preset(db, preset_id)
    selected_version = version or preset.current_version
    item = next((entry for entry in preset.versions if entry.version == selected_version), None)
    if not item:
        raise HTTPException(status_code=404, detail=f"Preset version not found: {selected_version}.")
    return parse_configuration_json(item.configuration_json), selected_version


def _validated_layer(configuration: dict[str, Any]) -> dict[str, Any]:
    try:
        validate_configuration_layer(configuration)
        validate_configuration_size(configuration)
        _validate_live_library_references(configuration)
        resolve_configuration(preset=configuration)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return sanitize_export(configuration)


def _resolved_out(
    resolved: ResolvedConfiguration,
    *,
    preset_id: str | None,
    preset_version: int | None,
    project_id: str | None,
) -> ConfigurationResolveOut:
    return ConfigurationResolveOut(
        configuration=resolved.configuration,
        provenance=resolved.provenance,
        resolved_at=resolved.resolved_at,
        application_defaults_version=resolved.application_defaults_version,
        preset_id=preset_id,
        preset_version=preset_version,
        project_id=project_id,
    )


def _preset_out(preset: ProductionPreset) -> ProductionPresetOut:
    versions = [
        ProductionPresetVersionOut(
            version=item.version,
            schema_version=item.schema_version,
            configuration=parse_configuration_json(item.configuration_json),
            change_note=item.change_note,
            created_at=item.created_at,
        )
        for item in preset.versions
    ]
    return ProductionPresetOut(
        id=preset.id,
        name=preset.name,
        description=preset.description,
        category=preset.category,
        cover_asset_id=preset.cover_asset_id,
        schema_version=preset.schema_version,
        current_version=preset.current_version,
        configuration=parse_configuration_json(preset.configuration_json),
        created_at=preset.created_at,
        updated_at=preset.updated_at,
        archived_at=preset.archived_at,
        versions=versions,
    )


def _project_out(project: Project) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        name=project.name,
        description=project.description,
        cover_asset_id=project.cover_asset_id,
        source_language=project.source_language,
        target_language=project.target_language,
        origin_preset_id=project.origin_preset_id,
        origin_preset_version=project.origin_preset_version,
        defaults_schema_version=project.defaults_schema_version,
        defaults_revision=project.defaults_revision,
        defaults=parse_configuration_json(project.defaults_json),
        glossary=parse_configuration_json(project.glossary_json),
        voice_cast=parse_configuration_json(project.voice_cast_json),
        video_count=len(project.video_memberships),
        created_at=project.created_at,
        updated_at=project.updated_at,
        archived_at=project.archived_at,
    )


def _project_video_out(membership: ProjectVideo) -> ProductionVideoOut:
    video = membership.video
    return ProductionVideoOut(
        id=video.id,
        project_id=membership.project_id,
        video_url=video.video_url,
        thumbnail_url=video.thumbnail_url,
        content=video.content,
        source_language=video.source_language,
        target_language=video.target_language,
        voice=video.voice,
        platform=video.platform,
        sort_order=membership.sort_order,
        overrides_revision=membership.overrides_revision,
        overrides=parse_configuration_json(membership.video_overrides_json),
        created_at=membership.created_at,
        updated_at=membership.updated_at,
    )


def _standalone_video_out(video: Video) -> ProductionVideoOut:
    return ProductionVideoOut(
        id=video.id,
        video_url=video.video_url,
        thumbnail_url=video.thumbnail_url,
        content=video.content,
        source_language=video.source_language,
        target_language=video.target_language,
        voice=video.voice,
        platform=video.platform,
        created_at=video.last_updated,
        updated_at=video.last_updated,
    )


def _require_preset(db: Session, preset_id: str) -> ProductionPreset:
    preset = db.get(ProductionPreset, preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Production preset not found.")
    return preset


def _require_project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


def _require_project_video(db: Session, project_id: str, video_id: str) -> ProjectVideo:
    membership = (
        db.query(ProjectVideo)
        .filter(ProjectVideo.project_id == project_id, ProjectVideo.video_id == video_id)
        .first()
    )
    if not membership:
        raise HTTPException(status_code=404, detail="Project video not found.")
    return membership


def _ensure_unique_preset_name(db: Session, name: str, exclude_id: str | None = None) -> None:
    query = db.query(ProductionPreset).filter(ProductionPreset.name == _display_text(name, 160))
    if exclude_id:
        query = query.filter(ProductionPreset.id != exclude_id)
    if query.first():
        raise HTTPException(status_code=409, detail="A production preset with this name already exists.")


def _available_copy_name(db: Session, name: str) -> str:
    base = _display_text(name, 140) or "Preset"
    candidate = f"{base} Copy"
    index = 2
    while db.query(ProductionPreset).filter(ProductionPreset.name == candidate).first():
        candidate = f"{base} Copy {index}"
        index += 1
    return candidate[:160]


def _display_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("\x00", "").split())
    return text[:limit]


def _validate_import_values(value: Any) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _validate_import_values(item)
    elif isinstance(value, list):
        for item in value:
            _validate_import_values(item)
    elif isinstance(value, str):
        lowered = value.casefold()
        if "\x00" in value or "../" in value or "..\\" in value or lowered.startswith("file://"):
            raise HTTPException(status_code=400, detail="Preset import contains an unsafe path.")


def _apply_voice_cast(layer: dict[str, Any], voice_cast: dict[str, Any]) -> None:
    if not voice_cast:
        return
    normalized: dict[str, dict[str, Any]] = {}
    for role, assignment in voice_cast.items():
        if isinstance(assignment, str):
            normalized[str(role)] = {"voice_id": assignment}
        elif isinstance(assignment, dict) and assignment.get("voice_id"):
            normalized[str(role)] = assignment
    if normalized:
        layer.setdefault("speakers", {})["voice_map"] = normalized


def _legacy_rate(rate: int) -> str:
    return f"{rate:+d}%"


def _validate_live_library_references(configuration: dict[str, Any]) -> None:
    from .services.production_configuration import NO_SUBTITLE_STYLE_ID
    from .services.subtitle_styles import list_subtitle_styles
    from .services.voice_options import list_voice_options

    known_voices = {item.id for item in list_voice_options()}
    voice_configuration = configuration.get("voice") or {}
    voice_id = str((voice_configuration.get("voice_id") or ""))
    if voice_id and voice_id not in known_voices:
        snapshot = voice_configuration.get("voice_snapshot") or {}
        if str(snapshot.get("id") or "") != voice_id:
            raise ValueError(f"Voice not found: {voice_id}.")
    for role, assignment in (((configuration.get("speakers") or {}).get("voice_map") or {}).items()):
        if isinstance(assignment, dict):
            role_voice = str(assignment.get("voice_id") or "")
            if role_voice and role_voice not in known_voices:
                snapshot = assignment.get("voice_snapshot") or {}
                if str(snapshot.get("id") or "") != role_voice:
                    raise ValueError(f"Voice not found for speaker role {role}: {role_voice}.")
    subtitle = configuration.get("subtitle") or {}
    style_id = str(subtitle.get("style_id") or "")
    if style_id and style_id != NO_SUBTITLE_STYLE_ID:
        known_styles = {str(item.get("id")) for item in list_subtitle_styles().get("styles") or []}
        if style_id not in known_styles:
            snapshot = subtitle.get("style_snapshot") or {}
            if not snapshot:
                raise ValueError(f"Subtitle style not found: {style_id}.")


def _assert_runtime_compatible(configuration: ProductionConfiguration) -> None:
    errors = validate_runtime_compatibility(configuration)
    if errors:
        raise HTTPException(status_code=409, detail={"message": "Runtime is incompatible.", "errors": errors})


def _audit(db: Session, entity_type: str, entity_id: str, action: str, message: str) -> None:
    db.add(
        AuditLog(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            message=message,
        )
    )

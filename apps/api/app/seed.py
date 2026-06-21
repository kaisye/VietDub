from __future__ import annotations

from sqlalchemy.orm import Session

from .models import MediaJob, Video, Voice, WorkflowTemplate
from .services.storage import ensure_storage


_LEGACY_DEMO_ROWS = [
    (
        "https://vimeo.com/729101923",
        "Exploring the future of generative media production for global teams.",
    ),
    (
        "https://storage.aether.local/social_teaser_01.mp4",
        "Nueva actualizacion del sistema con capacidades de localizacion.",
    ),
    (
        "https://youtube.com/watch?v=pending-demo",
        "Tutorial: How to configure your first Aether Studio localization run.",
    ),
]


def seed_database(db: Session) -> None:
    storage_root = ensure_storage()
    sample_output = storage_root / "rendered-outputs" / "sample-ready.mp4"
    if sample_output.exists():
        try:
            if sample_output.read_text(encoding="utf-8") == "Aether Studio seeded render artifact":
                sample_output.unlink()
        except (OSError, UnicodeDecodeError):
            pass

    # Early development builds populated every fresh database with three Aether
    # Studio demo jobs. Remove only the exact demo URL/content pairs so genuine
    # user jobs remain untouched, and do not create sample history for new users.
    for video_url, content in _LEGACY_DEMO_ROWS:
        db.query(MediaJob).filter(
            MediaJob.video_url == video_url,
            MediaJob.content == content,
        ).delete(synchronize_session=False)
        db.query(Video).filter(
            Video.video_url == video_url,
            Video.content == content,
        ).delete(synchronize_session=False)

    if not db.query(Voice).first():
        db.add_all(
            [
                Voice(name="James Deep", locale="en-US", style="Documentary"),
                Voice(name="Sarah Adams", locale="es-ES", style="Warm"),
                Voice(name="Robert B.", locale="fr-FR", style="Instructional"),
            ],
        )

    if not db.query(WorkflowTemplate).first():
        db.add_all(
            [
                WorkflowTemplate(name="Full Localization", description="Prepare a localized version from source media."),
                WorkflowTemplate(name="Quick Clip", description="Generate a short localized social clip."),
                WorkflowTemplate(name="Review First", description="Pause for human review before rendering."),
            ],
        )

    db.commit()

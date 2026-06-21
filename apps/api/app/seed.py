from __future__ import annotations

from sqlalchemy.orm import Session

from .models import MediaJob, MediaJobStatus, Video, VideoStatus, Voice, WorkflowTemplate
from .services.storage import ensure_storage


THUMBNAILS = [
    "https://images.unsplash.com/photo-1618005198919-d3d4b5a92ead?auto=format&fit=crop&w=360&q=80",
    "https://images.unsplash.com/photo-1635070041078-e363dbe005cb?auto=format&fit=crop&w=360&q=80",
    "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=360&q=80",
]


def seed_database(db: Session) -> None:
    storage_root = ensure_storage()
    sample_output = storage_root / "rendered-outputs" / "sample-ready.mp4"
    if not sample_output.exists():
        sample_output.write_text("Aether Studio seeded render artifact", encoding="utf-8")

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

    if not db.query(Video).first():
        db.add_all(
            [
                Video(
                    status=VideoStatus.processing,
                    video_url="https://vimeo.com/729101923",
                    thumbnail_url=THUMBNAILS[0],
                    content="Exploring the future of generative media production for global teams.",
                    source_language="EN",
                    target_language="DE",
                    voice="James Deep",
                    platform="YouTube",
                    publish_date="2026-06-04",
                    publish_time="14:30",
                    workflow_template="Full Localization",
                    progress=65,
                ),
                Video(
                    status=VideoStatus.published,
                    video_url="https://storage.aether.local/social_teaser_01.mp4",
                    thumbnail_url=THUMBNAILS[1],
                    content="Nueva actualizacion del sistema con capacidades de localizacion.",
                    source_language="ES",
                    target_language="EN",
                    voice="Sarah Adams",
                    platform="TikTok",
                    publish_date="2026-06-03",
                    publish_time="09:15",
                    workflow_template="Quick Clip",
                    progress=100,
                ),
                Video(
                    status=VideoStatus.queued,
                    video_url="https://youtube.com/watch?v=pending-demo",
                    thumbnail_url=THUMBNAILS[2],
                    content="Tutorial: How to configure your first Aether Studio localization run.",
                    source_language="EN",
                    target_language="FR",
                    voice="Robert B.",
                    platform="LinkedIn",
                    publish_date="2026-06-05",
                    publish_time="12:00",
                    workflow_template="Review First",
                    progress=5,
                ),
            ],
        )

    if not db.query(MediaJob).first():
        db.add_all(
            [
                MediaJob(
                    status=MediaJobStatus.draft,
                    video_url="https://vimeo.com/729101923",
                    content="Exploring the future of generative media production for global teams.",
                    source_language="EN",
                    target_language="DE",
                    voice="James Deep",
                    platform="YouTube",
                    publish_date="2026-06-04",
                    publish_time="14:30",
                    current_step="Draft",
                ),
                MediaJob(
                    status=MediaJobStatus.ready,
                    video_url="https://storage.aether.local/social_teaser_01.mp4",
                    content="Nueva actualizacion del sistema con capacidades de localizacion.",
                    source_language="ES",
                    target_language="EN",
                    voice="Sarah Adams",
                    platform="TikTok",
                    publish_date="2026-06-03",
                    publish_time="09:15",
                    progress=100,
                    current_step="Ready",
                    output_url="/storage/rendered-outputs/sample-ready.mp4",
                    logs="Seeded ready job.",
                ),
                MediaJob(
                    status=MediaJobStatus.queued,
                    video_url="https://youtube.com/watch?v=pending-demo",
                    content="Tutorial: How to configure your first Aether Studio localization run.",
                    source_language="EN",
                    target_language="FR",
                    voice="Robert B.",
                    platform="LinkedIn",
                    publish_date="2026-06-05",
                    publish_time="12:00",
                    progress=0,
                    current_step="Queued",
                ),
            ],
        )

    db.commit()

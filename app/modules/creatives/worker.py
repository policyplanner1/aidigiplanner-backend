import asyncio
from typing import Any

import structlog
from arq.connections import RedisSettings
from arq.worker import func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging_setup import configure_logging
from app.db.mixins import utcnow
from app.db.session import get_sessionmaker
from app.models.brand_profile import BrandProfile
from app.models.creative_asset import CreativeAsset
from app.models.creative_brief import CreativeBrief
from app.models.creative_concept import CreativeConcept
from app.models.enums import GenerationJobStatus, ReelStyle
from app.models.generation_job import GenerationJob
from app.modules.audit.service import AuditService
from app.modules.brand_profiles.resolution import resolve_effective_brand_profile
from app.modules.creatives.brand import BrandProfileDTO, brand_profile_from_row
from app.modules.creatives.domain import (
    REEL_LIKE_FORMATS,
    Angle,
    Brief,
    CarouselSlide,
    GeneratedConcept,
    OnImageText,
    ReelScript,
    video_backend_for,
)
from app.modules.creatives.pipeline.assemble import assemble_reel
from app.modules.creatives.pipeline.ideate import run_ideation
from app.modules.creatives.pipeline.render_image import render_images_for_concepts
from app.modules.creatives.pipeline.render_video import render_reel_clips_for_concepts
from app.modules.creatives.pipeline.types import RenderedAsset
from app.modules.creatives.posting_time import suggest_posting_time
from app.modules.creatives.pricing import get_creative_settings
from app.modules.creatives.providers.factory import (
    get_image_provider,
    get_llm_provider,
    get_video_provider,
)
from app.modules.storage.base import StorageService
from app.modules.storage.provider import get_storage_service

configure_logging()
logger = structlog.get_logger(__name__)

_ASSET_EXTENSION_BY_MIME = {"image/png": "png", "image/jpeg": "jpg", "video/mp4": "mp4"}


def _asset_storage_key(
    *, product_id: str, job_id: str, concept_index: int, asset: RenderedAsset
) -> str:
    ext = _ASSET_EXTENSION_BY_MIME.get(asset.mime_type, "bin")
    return f"{product_id}/{job_id}/{concept_index}/{asset.label}.{ext}"


async def _persist_asset(
    *,
    session: AsyncSession,
    storage: StorageService,
    job: GenerationJob,
    concept_rows: dict[int, CreativeConcept],
    asset: RenderedAsset,
) -> None:
    key = _asset_storage_key(
        product_id=job.product_id,
        job_id=job.id,
        concept_index=asset.concept_index,
        asset=asset,
    )
    await storage.save(key=key, data=asset.data, content_type=asset.mime_type)
    session.add(
        CreativeAsset(
            concept_id=concept_rows[asset.concept_index].id,
            kind=asset.kind,
            storage_key=key,
            label=asset.label,
            model_id=asset.model_id,
            mime_type=asset.mime_type,
            estimated_cost_inr=asset.estimated_cost_inr,
            actual_cost_inr=asset.actual_cost_inr,
            generated_at=utcnow(),
        )
    )


def _brief_from_row(brief_row: CreativeBrief) -> Brief:
    return Brief(
        product_line=brief_row.product_line,
        topic=brief_row.topic,
        format=brief_row.format,
        carousel_slides=brief_row.carousel_slides,
        reel_duration_s=brief_row.reel_duration_s,
        language=brief_row.language,
        concept_count=brief_row.concept_count,
        quality=brief_row.quality,
        extra_notes=brief_row.extra_notes,
        voiceover=brief_row.voiceover,
        objective=brief_row.objective,
        offer=brief_row.offer,
        festival_occasion=brief_row.festival_occasion,
        audience_override=brief_row.audience_override,
        tone_override=brief_row.tone_override,
        cta_override=brief_row.cta_override,
        reel_style=brief_row.reel_style,
    )


def _concept_from_row(row: CreativeConcept) -> GeneratedConcept:
    """Reconstructs the domain object the render pipeline expects from its
    persisted row -- needed because asset rendering (_run_asset_rendering)
    may run in a later arq task invocation than ideation (_run_ideation),
    with no in-memory IdeationResult to reuse (see GenerationJobStatus.
    awaiting_render's two-step reel flow)."""
    return GeneratedConcept(
        angle=Angle(row.angle),
        hook=row.hook,
        caption=row.caption,
        hashtags=row.hashtags,
        cta=row.cta,
        on_image_text=OnImageText(
            headline=row.on_image_headline,
            subhead=row.on_image_subhead,
            kicker=row.on_image_kicker,
        ),
        image_prompt=row.image_prompt,
        negative_prompt=row.negative_prompt,
        reel=ReelScript.model_validate(row.reel_script) if row.reel_script else None,
        carousel_slides=(
            [CarouselSlide.model_validate(s) for s in row.carousel_slides]
            if row.carousel_slides
            else None
        ),
        disclaimer_line=row.disclaimer_line,
        compliance_notes=row.compliance_notes,
        rejected=row.compliance_rejected,
        rejection_reason=row.compliance_rejection_reason,
    )


async def _load_brand(
    session: AsyncSession, product_id: str
) -> tuple[BrandProfile, BrandProfileDTO]:
    brand_row = await resolve_effective_brand_profile(session, product_id=product_id)
    return brand_row, brand_profile_from_row(brand_row)


async def _run_ideation(session: AsyncSession, job_id: str, storage: StorageService) -> None:
    """Phase 1: LLM ideation + the compliance gate. All-or-nothing --
    without concepts there's nothing downstream worth salvaging, so any
    failure here fails the whole job. Concepts are committed (not just
    flushed) so they survive a later asset-rendering failure instead of
    being rolled back along with it -- ideation already cost real money and
    the user shouldn't lose it because image/video rendering hit a
    transient error afterward (or, for reels, hasn't even run yet -- see
    GenerationJobStatus.awaiting_render below).

    For post/carousel formats, asset rendering runs immediately afterward
    in the same task (unchanged single-call behavior). For reels, the job
    instead stops at awaiting_render and waits for an explicit
    render-assets call -- see CreativeService.render_assets and
    _run_asset_rendering."""
    job = await session.get(GenerationJob, job_id)
    if job is None:
        logger.warning("generation_job.missing", job_id=job_id)
        return

    audit = AuditService(session)
    job.status = GenerationJobStatus.running
    job.started_at = utcnow()
    await session.commit()

    try:
        brief_row = await session.get(CreativeBrief, job.brief_id)
        if brief_row is None:
            raise ValueError(f"creative brief {job.brief_id} missing for job {job_id}")

        _, brand = await _load_brand(session, job.product_id)
        brief = _brief_from_row(brief_row)

        creative_settings = get_creative_settings()
        llm = get_llm_provider(dry_run=job.dry_run, settings=creative_settings)

        # run_ideation (and every provider call it makes) is synchronous --
        # offload the whole call so a slow/real Gemini call never blocks the
        # worker's event loop.
        ideation = await asyncio.to_thread(run_ideation, brief, brand, llm, creative_settings)

        # Persisted in original generation order (not split by
        # accepted/rejected) -- compliance-rejected concepts are stored too,
        # with their reason, never silently dropped.
        suggested_time = suggest_posting_time(brief_row.platforms, utcnow())
        for idx, concept in enumerate(ideation.concepts):
            session.add(
                CreativeConcept(
                    job_id=job.id,
                    concept_index=idx,
                    angle=concept.angle.value,
                    hook=concept.hook,
                    caption=concept.caption,
                    hashtags=concept.hashtags,
                    cta=concept.cta,
                    on_image_headline=concept.on_image_text.headline,
                    on_image_subhead=concept.on_image_text.subhead,
                    on_image_kicker=concept.on_image_text.kicker,
                    image_prompt=concept.image_prompt,
                    negative_prompt=concept.negative_prompt,
                    reel_script=concept.reel.model_dump() if concept.reel else None,
                    carousel_slides=(
                        [slide.model_dump() for slide in concept.carousel_slides]
                        if concept.carousel_slides
                        else None
                    ),
                    disclaimer_line=concept.disclaimer_line,
                    compliance_notes=concept.compliance_notes,
                    compliance_rejected=concept.rejected,
                    compliance_rejection_reason=concept.rejection_reason,
                    suggested_posting_time=suggested_time,
                )
            )

        if brief.format in REEL_LIKE_FORMATS:
            job.status = GenerationJobStatus.awaiting_render
            await audit.log(
                action="creative_job.awaiting_render",
                actor_user_id=job.requested_by,
                company_id=job.company_id,
                product_id=job.product_id,
                resource_type="generation_job",
                resource_id=job.id,
                metadata={"accepted": len(ideation.accepted), "rejected": len(ideation.rejected)},
            )
            await session.commit()
            logger.info("generation_job.awaiting_render", job_id=job_id)
            return

        await session.commit()
    except asyncio.CancelledError:
        # arq force-cancels a task once it exceeds WorkerSettings' job_timeout
        # (see the func() timeout overrides below) by raising CancelledError
        # *inside* this coroutine -- a BaseException, not an Exception, so it
        # would silently skip the `except Exception` branch below and leave
        # the job stuck at `running` forever with no error if not handled
        # here explicitly. Record the timeout, then re-raise so the task
        # still completes its cancellation as arq expects.
        await session.rollback()
        job = await session.get(GenerationJob, job_id)
        if job is not None:
            job.status = GenerationJobStatus.failed
            job.error_message = "Generation timed out."
            job.finished_at = utcnow()
            await session.commit()
        logger.error("generation_job.timed_out", job_id=job_id, stage="ideation")
        raise
    except Exception as exc:
        await session.rollback()
        job = await session.get(GenerationJob, job_id)
        if job is not None:
            job.status = GenerationJobStatus.failed
            job.error_message = str(exc)[:2000]
            job.finished_at = utcnow()
            await audit.log(
                action="creative_job.failed",
                actor_user_id=job.requested_by,
                company_id=job.company_id,
                product_id=job.product_id,
                resource_type="generation_job",
                resource_id=job.id,
                metadata={"stage": "ideation", "error": str(exc)[:500]},
            )
            await session.commit()
        logger.exception("generation_job.failed", job_id=job_id, stage="ideation")
        return

    # Post/carousel: render assets immediately, same task, same commit
    # boundary as before this job's ideation-vs-rendering split existed.
    await _run_asset_rendering(session, job_id, storage)


async def _run_asset_rendering(session: AsyncSession, job_id: str, storage: StorageService) -> None:
    """Phase 2: image rendering for every accepted concept (post/carousel
    slides, or a reel's cover/first-frame), and for reels, per-scene video
    clips + ffmpeg assembly. A failure here degrades the job to
    partially_failed rather than discarding the ideation work committed by
    _run_ideation.

    Rebuilds brief/brand/concepts from the DB rather than reusing in-memory
    state from _run_ideation, since for reels this runs in a separate,
    later arq task triggered by POST .../render-assets -- there is no
    guarantee it's even the same worker process."""
    job = await session.get(GenerationJob, job_id)
    if job is None:
        logger.warning("generation_job.missing", job_id=job_id)
        return

    audit = AuditService(session)
    job.status = GenerationJobStatus.running
    await session.commit()

    rendered_assets: list[RenderedAsset] = []
    try:
        brief_row = await session.get(CreativeBrief, job.brief_id)
        if brief_row is None:
            raise ValueError(f"creative brief {job.brief_id} missing for job {job_id}")
        brand_row, brand = await _load_brand(session, job.product_id)
        brief = _brief_from_row(brief_row)
        creative_settings = get_creative_settings()

        concept_row_list = list(
            await session.scalars(
                select(CreativeConcept)
                .where(CreativeConcept.job_id == job.id, CreativeConcept.deleted_at.is_(None))
                .order_by(CreativeConcept.concept_index)
            )
        )
        concept_rows: dict[int, CreativeConcept] = {r.concept_index: r for r in concept_row_list}
        concepts: dict[int, GeneratedConcept] = {
            idx: _concept_from_row(row) for idx, row in concept_rows.items()
        }

        logo_bytes: bytes | None = None
        if brand_row.logo_storage_key:
            logo_bytes = await storage.read(brand_row.logo_storage_key)

        # Only accepted concepts get rendered -- no point spending on images
        # for concepts that already failed compliance.
        indexed_accepted = [(idx, c) for idx, c in concepts.items() if not c.rejected]
        if indexed_accepted:
            image_provider = get_image_provider(dry_run=job.dry_run, settings=creative_settings)
            rendered_assets = await asyncio.to_thread(
                render_images_for_concepts,
                indexed_accepted,
                format=brief.format,
                quality=brief.quality,
                carousel_slides=brief.carousel_slides,
                brand=brand,
                image_provider=image_provider,
                logo_bytes=logo_bytes,
            )
            for asset in rendered_assets:
                await _persist_asset(
                    session=session,
                    storage=storage,
                    job=job,
                    concept_rows=concept_rows,
                    asset=asset,
                )

        # Reels get a video-rendering pass on top of the cover image every
        # format already gets above. Story reels use each concept's own
        # cover as the scene reference; avatar reels use the one shared
        # brand-profile avatar image for every concept instead (validated
        # to exist at request time -- see CreativeService.request_generation).
        if brief.format in REEL_LIKE_FORMATS and indexed_accepted:
            reel_concepts = [(idx, c) for idx, c in indexed_accepted if c.reel is not None]
        else:
            reel_concepts = []

        if reel_concepts:
            if brief_row.reel_style == ReelStyle.avatar:
                assert brand_row.avatar_storage_key is not None
                avatar_bytes = await storage.read(brand_row.avatar_storage_key)
                reference_images = {idx: avatar_bytes for idx, _ in reel_concepts}
            else:
                cover_bytes_by_index = {
                    a.concept_index: a.data for a in rendered_assets if a.label == "cover"
                }
                reference_images = {
                    idx: cover_bytes_by_index[idx]
                    for idx, _ in reel_concepts
                    if idx in cover_bytes_by_index
                }

            # Brief's format-specific validator guarantees voiceover is set
            # whenever format is reel -- see domain.Brief._check_format_specific_fields.
            assert brief.voiceover is not None
            backend = video_backend_for(brief.voiceover)
            video_provider = get_video_provider(
                dry_run=job.dry_run, backend=backend, settings=creative_settings
            )

            # Both the per-scene generation calls and the ffmpeg assembly
            # below are chains of blocking calls -- each offloaded via
            # asyncio.to_thread so neither blocks the worker's event loop.
            raw_clip_assets = await asyncio.to_thread(
                render_reel_clips_for_concepts,
                reel_concepts,
                reference_images=reference_images,
                quality=brief.quality,
                voiceover=brief.voiceover,
                video_provider=video_provider,
            )
            for asset in raw_clip_assets:
                await _persist_asset(
                    session=session,
                    storage=storage,
                    job=job,
                    concept_rows=concept_rows,
                    asset=asset,
                )

            clips_by_concept: dict[int, list[RenderedAsset]] = {}
            for clip in raw_clip_assets:
                clips_by_concept.setdefault(clip.concept_index, []).append(clip)

            for idx, concept in reel_concepts:
                scene_clips = sorted(clips_by_concept.get(idx, []), key=lambda a: a.label)
                if not scene_clips:
                    continue
                reel_asset = await asyncio.to_thread(
                    assemble_reel,
                    concept,
                    idx,
                    scene_clip_bytes=[c.data for c in scene_clips],
                    brand=brand,
                    logo_bytes=logo_bytes,
                )
                await _persist_asset(
                    session=session,
                    storage=storage,
                    job=job,
                    concept_rows=concept_rows,
                    asset=reel_asset,
                )
                rendered_assets.append(reel_asset)
            rendered_assets.extend(raw_clip_assets)

        job.status = GenerationJobStatus.succeeded
        job.finished_at = utcnow()
        accepted_count = sum(1 for c in concepts.values() if not c.rejected)
        rejected_count = len(concepts) - accepted_count
        await audit.log(
            action="creative_job.succeeded",
            actor_user_id=job.requested_by,
            company_id=job.company_id,
            product_id=job.product_id,
            resource_type="generation_job",
            resource_id=job.id,
            metadata={
                "accepted": accepted_count,
                "rejected": rejected_count,
                "assets": len(rendered_assets),
            },
        )
        await session.commit()
        logger.info(
            "generation_job.succeeded",
            job_id=job_id,
            accepted=accepted_count,
            rejected=rejected_count,
            assets=len(rendered_assets),
        )
    except asyncio.CancelledError:
        # See _run_ideation's matching handler -- arq's job_timeout cancels
        # this coroutine via CancelledError (a BaseException, invisible to
        # `except Exception` below), which would otherwise leave the job
        # stuck at `running` forever with no error. Record the timeout
        # (degraded, not empty, if any assets rendered before the cutoff)
        # and re-raise so the task still completes its cancellation.
        await session.rollback()
        job = await session.get(GenerationJob, job_id)
        if job is not None:
            job.status = (
                GenerationJobStatus.partially_failed if rendered_assets else GenerationJobStatus.failed
            )
            job.error_message = "Rendering timed out."
            job.finished_at = utcnow()
            await session.commit()
        logger.error("generation_job.timed_out", job_id=job_id, stage="asset_rendering")
        raise
    except Exception as exc:
        # Discards any partial CreativeAsset adds from this phase only --
        # the concepts committed by _run_ideation are unaffected, since that
        # commit already landed in a prior transaction. Re-fetch the job
        # (rollback expires it) to record the degraded-but-not-empty result
        # as partially_failed rather than failed.
        await session.rollback()
        job = await session.get(GenerationJob, job_id)
        if job is not None:
            job.status = GenerationJobStatus.partially_failed
            job.error_message = str(exc)[:2000]
            job.finished_at = utcnow()
            await audit.log(
                action="creative_job.partially_failed",
                actor_user_id=job.requested_by,
                company_id=job.company_id,
                product_id=job.product_id,
                resource_type="generation_job",
                resource_id=job.id,
                metadata={"stage": "asset_rendering", "error": str(exc)[:500]},
            )
            await session.commit()
        logger.exception("generation_job.partially_failed", job_id=job_id, stage="asset_rendering")


async def generate_creatives_job(ctx: dict[str, Any], job_id: str) -> None:
    """The arq task that runs LLM ideation + the compliance gate for one
    generation job. For post/carousel formats it continues straight into
    asset rendering in the same call (unchanged end-to-end behavior); for
    reels it stops at GenerationJobStatus.awaiting_render and waits for a
    separate render_creative_assets_job, triggered by POST
    .../creatives/jobs/{id}/render-assets.

    Runs in a separate worker process with no HTTP request context, so by
    default it opens its own DB session via get_sessionmaker() and the real
    get_storage_service() -- the same pattern scripts/seed_super_admin.py
    uses to call service-layer code outside a request. tests/fakes.py's
    FakeArqPool instead stashes the test's own db_session fixture and an
    InMemoryStorageService on ctx["session"]/ctx["storage"], so integration
    tests see the job through the same (savepoint-scoped, uncommitted-to-
    the-real-DB) session the HTTP request used to create it, and read
    generated assets back from the same in-memory store the worker wrote to
    -- rather than a real get_storage_service() call (not routed through
    FastAPI's DI, so dependency_overrides can't reach it here) writing to
    real disk that the test's HTTP-layer download call can't see.
    """
    session = ctx.get("session")
    storage = ctx.get("storage")
    if session is not None and storage is not None:
        await _run_ideation(session, job_id, storage)
        return

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as owned_session:
        await _run_ideation(owned_session, job_id, storage or get_storage_service())


async def render_creative_assets_job(ctx: dict[str, Any], job_id: str) -> None:
    """The arq task that renders images/video for a job already sitting at
    GenerationJobStatus.awaiting_render -- see generate_creatives_job's
    docstring and CreativeService.render_assets. Session/storage resolution
    mirrors generate_creatives_job exactly, for the same testing reasons."""
    session = ctx.get("session")
    storage = ctx.get("storage")
    if session is not None and storage is not None:
        await _run_asset_rendering(session, job_id, storage)
        return

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as owned_session:
        await _run_asset_rendering(owned_session, job_id, storage or get_storage_service())


WORKER_FUNCTIONS = {
    "generate_creatives_job": generate_creatives_job,
    "render_creative_assets_job": render_creative_assets_job,
}


class WorkerSettings:
    """Run with: uv run arq app.modules.creatives.worker.WorkerSettings"""

    # arq's default job_timeout (300s) is enough for ideation (a single LLM
    # call) but not for asset rendering -- a reel's per-scene Veo/Omni video
    # generation plus ffmpeg assembly routinely runs past 5 minutes. A job
    # that hits job_timeout gets force-cancelled by arq; see the
    # `except asyncio.CancelledError` handlers in _run_ideation/
    # _run_asset_rendering for what happens if that still occurs.
    functions = [
        func(generate_creatives_job, timeout=600),
        func(render_creative_assets_job, timeout=1800),
    ]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)

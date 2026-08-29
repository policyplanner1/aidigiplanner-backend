"""ReelScript -> per-scene raw video clips. Ported from the prototype's
app/pipeline/render_video.py, adapted to return in-memory RenderedAsset
bytes instead of writing files, and to accept a caller-supplied reference
image per concept rather than always using the cover image.

Scene 1 uses the reel's reference image as its literal first frame
(image-to-video); later scenes use that same image as a style reference
instead -- Veo's SDK doesn't allow first-frame image-to-video and
reference_images on the same call, and the two mean different things
anyway (scene 1 must *start* from that exact frame; scenes 2+ only need to
*look* consistent with it).

For a story-style reel, the reference image is the concept's own
already-rendered cover image (visual continuity with the static post). For
an avatar-style reel, it's the product's uploaded brand-profile avatar
image, reused across every concept, so the same face appears throughout
-- render_reel_clips itself doesn't know or care which case it's in; the
worker decides what to pass as `reference_image_bytes`.
"""

from __future__ import annotations

from app.models.enums import CreativeAssetKind, CreativeQuality, VoiceoverMode
from app.modules.creatives.domain import GeneratedConcept
from app.modules.creatives.pipeline.types import VIDEO_MIME_TYPE, RenderedAsset
from app.modules.creatives.providers.base import VideoProvider


def render_reel_clips(
    concept: GeneratedConcept,
    concept_index: int,
    *,
    reference_image_bytes: bytes,
    quality: CreativeQuality,
    voiceover: VoiceoverMode,
    video_provider: VideoProvider,
) -> list[RenderedAsset]:
    if concept.reel is None:
        raise ValueError(f"concept {concept_index} has no reel script")

    assets: list[RenderedAsset] = []
    for i, scene in enumerate(concept.reel.scenes):
        is_first = i == 0
        result = video_provider.generate_clip(
            scene=scene,
            aspect_ratio="9:16",
            quality=quality,
            voiceover=voiceover,
            first_frame_image=reference_image_bytes if is_first else None,
            reference_images=None if is_first else [reference_image_bytes],
        )
        if result.video_bytes is None:
            raise RuntimeError(
                f"{video_provider.backend_name} provider returned no video bytes for "
                f"concept {concept_index}, scene {i}"
            )
        assets.append(
            RenderedAsset(
                kind=CreativeAssetKind.raw_clip,
                concept_index=concept_index,
                label=f"scene_{i + 1:02d}",
                model_id=result.model_id,
                mime_type=VIDEO_MIME_TYPE,
                data=result.video_bytes,
                estimated_cost_inr=result.estimated_cost_inr,
            )
        )
    return assets


def render_reel_clips_for_concepts(
    indexed_concepts: list[tuple[int, GeneratedConcept]],
    *,
    reference_images: dict[int, bytes],
    quality: CreativeQuality,
    voiceover: VoiceoverMode,
    video_provider: VideoProvider,
) -> list[RenderedAsset]:
    """reference_images maps concept_index -> the image bytes that concept's
    scenes should be keyed off of (cover image for story reels, the shared
    brand avatar for avatar reels -- see module docstring)."""
    assets: list[RenderedAsset] = []
    for concept_index, concept in indexed_concepts:
        if concept.reel is None:
            continue
        reference = reference_images.get(concept_index)
        if reference is None:
            raise ValueError(f"no reference image available for concept {concept_index}")
        assets.extend(
            render_reel_clips(
                concept,
                concept_index,
                reference_image_bytes=reference,
                quality=quality,
                voiceover=voiceover,
                video_provider=video_provider,
            )
        )
    return assets

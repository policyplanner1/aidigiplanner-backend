"""GeneratedConcept -> post/carousel images -- ported from the prototype's
app/pipeline/render_image.py, adapted to return in-memory RenderedAsset
bytes (see pipeline/types.py) instead of writing files under a shared
downloads/ directory; the async worker persists them via StorageService.

Single post/reel cover: one image per concept.
Carousel: slide 1 is generated fresh; slides 2..N pass slide 1's bytes as a
reference image so typography and palette stay consistent across the set
(reference-image chaining, not independent per-slide calls).

Post-processing (logo composite, disclaimer strip, optional safe-zone
overlay) always runs, on every image, from every provider (mock or real).
"""

from __future__ import annotations

from app.models.enums import CreativeAssetKind, CreativeFormat, CreativeQuality
from app.modules.creatives.brand import BrandProfileDTO
from app.modules.creatives.domain import ASPECT_RATIO_BY_FORMAT, GeneratedConcept, OnImageText
from app.modules.creatives.pipeline.postprocess import compose_final_image
from app.modules.creatives.pipeline.types import RenderedAsset
from app.modules.creatives.pricing import IMAGE_RESOLUTION_BY_QUALITY
from app.modules.creatives.providers.base import ImageProvider

_QUALITY_DIRECTIVES = (
    "Make this look like a professional, polished piece of design work fit for a "
    "premium brand -- not an amateur or generic AI-generated graphic.\n"
    "Reflect current Instagram creative design trends: confident modern typography, "
    "deliberate use of whitespace, and contemporary layout conventions -- not a dated "
    "stock-template look."
)


def _with_on_image_text(base_prompt: str, text: OnImageText) -> str:
    """Guaranteed on every image call, regardless of what the ideation LLM's
    freeform image_prompt happened to include on its own: the given
    headline/subhead/kicker actually render on the image, and a
    professional/on-trend quality bar is requested."""
    lines = [f'Headline text to render on the image, large and legible: "{text.headline}"']
    if text.subhead:
        lines.append(f'Subhead text, smaller, beneath the headline: "{text.subhead}"')
    if text.kicker:
        lines.append(f'Kicker text, small, above the headline: "{text.kicker}"')
    lines.append(
        "Render this text directly baked into the image, in the brand's typography style. "
        "Do not add any other text, caption, or watermark beyond what's specified above."
    )
    return f"{base_prompt}\n\n" + "\n".join(lines) + f"\n\n{_QUALITY_DIRECTIVES}"


def render_post_or_cover(
    concept: GeneratedConcept,
    concept_index: int,
    *,
    format: CreativeFormat,
    quality: CreativeQuality,
    brand: BrandProfileDTO,
    image_provider: ImageProvider,
    logo_bytes: bytes | None = None,
    debug_safezone: bool = False,
) -> RenderedAsset:
    """One image for a single post, or the reel cover / first-frame image."""
    aspect_ratio = ASPECT_RATIO_BY_FORMAT[format]
    resolution = IMAGE_RESOLUTION_BY_QUALITY[quality]
    result = image_provider.generate_image(
        prompt=_with_on_image_text(concept.image_prompt, concept.on_image_text),
        negative_prompt=concept.negative_prompt,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        quality=quality,
    )
    final_bytes = compose_final_image(
        result.image_bytes,
        disclaimer=concept.disclaimer_line,
        logo_bytes=logo_bytes,
        debug_safezone=debug_safezone,
    )
    # A reel's cover image is also its video's first frame -- label it
    # accordingly so it doesn't read as an unrelated static post output.
    label = "cover" if format is CreativeFormat.reel else "post"
    return RenderedAsset(
        kind=CreativeAssetKind.image,
        concept_index=concept_index,
        label=label,
        model_id=result.model_id,
        mime_type=result.mime_type,
        data=final_bytes,
        estimated_cost_inr=result.estimated_cost_inr,
    )


def _slide_content(concept: GeneratedConcept, slide_num: int) -> tuple[str, OnImageText]:
    """Prefers the concept's own per-slide story-arc content
    (concept.carousel_slides, populated by the ideation prompt for carousel
    format); falls back to the shared image_prompt/on_image_text if the LLM
    didn't populate it -- better a repeated slide than a crash."""
    slides = concept.carousel_slides
    if slides and len(slides) >= slide_num:
        slide = slides[slide_num - 1]
        return slide.visual_prompt, OnImageText(headline=slide.headline, subhead=slide.subhead)
    return concept.image_prompt, concept.on_image_text


def render_carousel(
    concept: GeneratedConcept,
    concept_index: int,
    *,
    slide_count: int,
    quality: CreativeQuality,
    brand: BrandProfileDTO,
    image_provider: ImageProvider,
    logo_bytes: bytes | None = None,
    debug_safezone: bool = False,
) -> list[RenderedAsset]:
    """Slide 1 is a fresh generation; slides 2..N reference slide 1's bytes
    for palette/typography consistency across the set, while each slide
    gets its own visual_prompt/headline (see _slide_content) so the set
    tells a story instead of repeating the same image three times."""
    aspect_ratio = ASPECT_RATIO_BY_FORMAT[CreativeFormat.carousel]
    resolution = IMAGE_RESOLUTION_BY_QUALITY[quality]

    assets: list[RenderedAsset] = []
    slide_1_bytes: bytes | None = None

    for slide_num in range(1, slide_count + 1):
        base_prompt, on_image_text = _slide_content(concept, slide_num)
        if slide_num == 1:
            prompt = _with_on_image_text(base_prompt, on_image_text)
            reference_images = None
        else:
            base_prompt = (
                f"{base_prompt}\n\nThis is slide {slide_num} of {slide_count} in the same "
                "carousel. Match the reference image's typography, palette, and composition "
                "style exactly, while depicting this slide's own content described above."
            )
            prompt = _with_on_image_text(base_prompt, on_image_text)
            reference_images = [slide_1_bytes] if slide_1_bytes is not None else None

        result = image_provider.generate_image(
            prompt=prompt,
            negative_prompt=concept.negative_prompt,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            quality=quality,
            reference_images=reference_images,
        )
        if slide_num == 1:
            slide_1_bytes = result.image_bytes

        final_bytes = compose_final_image(
            result.image_bytes,
            disclaimer=concept.disclaimer_line,
            logo_bytes=logo_bytes,
            debug_safezone=debug_safezone,
        )
        assets.append(
            RenderedAsset(
                kind=CreativeAssetKind.image,
                concept_index=concept_index,
                label=f"slide_{slide_num:02d}",
                model_id=result.model_id,
                mime_type=result.mime_type,
                data=final_bytes,
                estimated_cost_inr=result.estimated_cost_inr,
            )
        )
    return assets


def render_images_for_concepts(
    indexed_concepts: list[tuple[int, GeneratedConcept]],
    *,
    format: CreativeFormat,
    quality: CreativeQuality,
    carousel_slides: int | None,
    brand: BrandProfileDTO,
    image_provider: ImageProvider,
    logo_bytes: bytes | None = None,
    debug_safezone: bool = False,
) -> list[RenderedAsset]:
    """Renders images for the given (original_index, concept) pairs -- the
    caller passes only accepted concepts, each still carrying its original
    position in the full ideation.concepts list so the resulting assets can
    be matched back to the right persisted CreativeConcept row."""
    assets: list[RenderedAsset] = []
    for concept_index, concept in indexed_concepts:
        if format is CreativeFormat.carousel:
            assert carousel_slides is not None
            assets.extend(
                render_carousel(
                    concept,
                    concept_index,
                    slide_count=carousel_slides,
                    quality=quality,
                    brand=brand,
                    image_provider=image_provider,
                    logo_bytes=logo_bytes,
                    debug_safezone=debug_safezone,
                )
            )
        else:
            assets.append(
                render_post_or_cover(
                    concept,
                    concept_index,
                    format=format,
                    quality=quality,
                    brand=brand,
                    image_provider=image_provider,
                    logo_bytes=logo_bytes,
                    debug_safezone=debug_safezone,
                )
            )
    return assets
